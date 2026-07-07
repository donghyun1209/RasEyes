"""PiperTts — piper-tts 기반 비동기 TTS 구현체."""
import logging
import subprocess
import threading
import time
from typing import Optional

import config
from audio.tts_hal import BaseTtsHAL
from fusion.engine import RiskLevel

logger = logging.getLogger(__name__)

# --- Piper pygoruut PhonemeType Monkey Patch ---
try:
    import piper.config
    import piper.voice
    from enum import Enum

    class PatchedPhonemeType(str, Enum):
        ESPEAK = "espeak"
        TEXT = "text"
        PINYIN = "pinyin"
        PYGORUUT = "pygoruut"

    piper.config.PhonemeType = PatchedPhonemeType
    piper.voice.PhonemeType = PatchedPhonemeType

    original_phonemize = piper.voice.PiperVoice.phonemize

    def patched_phonemize(self, text: str) -> list[list[str]]:
        if self.config.phoneme_type == PatchedPhonemeType.PYGORUUT:
            from pygoruut.pygoruut import Pygoruut
            if not hasattr(self, "_pygoruut_phonemizer"):
                self._pygoruut_phonemizer = Pygoruut()

            lang = self.config.espeak_voice  # "Korean"
            phonemes_str = self._pygoruut_phonemizer.phonemize(language=lang, sentence=text)
            phoneme_list = list(str(phonemes_str))
            return [phoneme_list]
        return original_phonemize(self, text)

    piper.voice.PiperVoice.phonemize = patched_phonemize
except Exception as patch_err:
    logger.warning("piper-tts pygoruut 몽키 패치 적용 실패/스킵 (pip 미설치 등): %s", patch_err)


class PiperTts(BaseTtsHAL):
    """piper-tts 신경망 TTS 구현체.

    모델을 init 시 한 번만 로딩하고, 이후 발화는 인메모리 추론으로 처리한다.
    HIGH/MID 쿨다운 및 HIGH 우선 선점 정책은 EspeakTts와 동일하다.

    사전 조건:
        pip install piper-tts
        config.TTS_PIPER_MODEL_PATH 경로에 .onnx 및 .onnx.json 파일 필요.
        다운로드: scripts/download_piper_model.sh 참조.
    """

    def __init__(self, model_path: str, device_idx: Optional[int] = None) -> None:
        from piper.voice import PiperVoice  # 런타임 import — 미설치 시 fallback 처리

        self._voice = PiperVoice.load(model_path, use_cuda=False)
        self._device_idx = device_idx
        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._current_proc: Optional[subprocess.Popen] = None
        self._last_high_time: float = 0.0
        self._last_mid_time: float = 0.0

    def speak(self, text: str, risk_level: RiskLevel = RiskLevel.HIGH) -> None:
        """텍스트를 비동기로 발화한다.

        Args:
            text: 발화할 문자열.
            risk_level: HIGH면 진행 중 발화를 교체하고 즉시 발화.
                        MID면 발화 중이면 skip.
        """
        now = time.monotonic()
        if risk_level == RiskLevel.HIGH:
            if now - self._last_high_time < config.TTS_HIGH_COOLDOWN_SEC:
                return
            self._kill_current()
            self._last_high_time = now
            self._start_thread(text)
        elif risk_level == RiskLevel.MID:
            if now - self._last_mid_time < config.TTS_MID_COOLDOWN_SEC:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._last_mid_time = now
            self._start_thread(text)

    def stop(self) -> None:
        """진행 중인 발화를 중단하고 리소스를 해제한다."""
        self._kill_current()

    def is_speaking(self) -> bool:
        """현재 발화 스레드가 실행 중이면 True를 반환한다."""
        return self._thread is not None and self._thread.is_alive()

    def _kill_current(self) -> None:
        """실행 중인 발화 스레드와 aplay 프로세스를 즉시 종료한다.

        stop_flag를 세팅하고 current_proc을 SIGKILL로 즉시 종료한다.
        proc.kill()은 blocking write 중인 스레드에서 BrokenPipeError를 유발하여
        스레드를 빠르게 깨운다. 이후 join(0.5s)으로 종료를 확인한다.

        clear() 대신 새 Event를 할당하여 이전/신규 스레드의 stop 신호를 격리한다.
        단, _speak_worker는 stop_flag를 self가 아닌 인자로 받으므로 교체 후에도
        이전 스레드가 올바른 Event를 참조한다.
        """
        if self._thread is not None and self._thread.is_alive():
            self._stop_flag.set()
            proc = self._current_proc
            if proc is not None and proc.poll() is None:
                proc.kill()  # SIGKILL — 즉시 종료
            self._thread.join(timeout=0.5)
        self._current_proc = None
        self._stop_flag = threading.Event()
        self._thread = None

    def _start_thread(self, text: str) -> None:
        """발화 스레드를 새로 생성하고 시작한다.

        stop_flag를 인자로 캡처하여 _kill_current()가 Event를 교체해도
        이전 스레드가 올바른 Event를 참조하도록 한다.

        Args:
            text: 발화할 문자열.
        """
        stop_flag = self._stop_flag  # 현재 Event 캡처 — 교체 후에도 유효
        self._thread = threading.Thread(
            target=self._speak_worker,
            args=(text, stop_flag),
            daemon=True,
            name="tts-worker",
        )
        self._thread.start()

    def _speak_worker(self, text: str, stop_flag: threading.Event) -> None:
        """Piper로 PCM을 합성하고 aplay subprocess로 재생한다.

        Args:
            text: 발화할 문자열.
            stop_flag: 이 스레드 전용 정지 플래그 (self._stop_flag와 독립).
        """
        # 합성 중 선점 신호 감지
        try:
            pcm_chunks = []
            if hasattr(self._voice, "synthesize_stream_raw"):
                for chunk in self._voice.synthesize_stream_raw(text):
                    if stop_flag.is_set():
                        return
                    pcm_chunks.append(chunk)
            else:
                for chunk in self._voice.synthesize(text):
                    if stop_flag.is_set():
                        return
                    if hasattr(chunk, "audio_int16_bytes"):
                        pcm_chunks.append(chunk.audio_int16_bytes)
                    else:
                        pcm_chunks.append(chunk)
            pcm_bytes = b"".join(pcm_chunks)
        except Exception as exc:
            logger.warning("Piper TTS 추론 실패: %s", exc)
            return

        if stop_flag.is_set():
            return

        # aplay subprocess — ALSA dmix와 완전 호환
        # _current_proc을 write() 전에 저장해야 _kill_current()가 blocking write를 강제 종료할 수 있다.
        try:
            sample_rate = self._voice.config.sample_rate
            proc = subprocess.Popen(
                ["aplay", "-D", "default", "-f", "S16_LE",
                 "-r", str(sample_rate), "-c", "1", "--quiet"],
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self._current_proc = proc
            try:
                proc.stdin.write(pcm_bytes)
                proc.stdin.close()
            except BrokenPipeError:
                return  # _kill_current()가 proc를 kill해서 파이프 종료
            while proc.poll() is None:
                if stop_flag.is_set():
                    proc.kill()
                    break
                time.sleep(0.05)
        except Exception as exc:
            logger.warning("TTS 재생 실패: %s", exc)
