"""PiperTts — piper-tts 기반 비동기 TTS 구현체."""
import logging
import threading
import time
from collections import OrderedDict
from typing import Optional

import numpy as np

import config
from audio.interface import BaseTtsHAL
from audio.prerendered_tts import load_prerendered_cache
from audio.resident_stream import ResidentAudioStream
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

# --- ONNX Runtime CPU 스레드 제한 몽키 패치 (보조배터리 OCP 방지) ---
# PiperVoice.load()가 onnxruntime.SessionOptions()를 옵션 없이 생성해 모든 CPU 코어를
# 사용하므로, 순간 전류 스파이크를 막기 위해 생성자를 감싸 스레드 수를 강제한다.
try:
    import onnxruntime

    _OriginalSessionOptions = onnxruntime.SessionOptions

    class _ThreadLimitedSessionOptions(_OriginalSessionOptions):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.intra_op_num_threads = config.TTS_ONNX_INTRA_OP_THREADS
            self.inter_op_num_threads = config.TTS_ONNX_INTER_OP_THREADS

    onnxruntime.SessionOptions = _ThreadLimitedSessionOptions
except Exception as patch_err:
    logger.warning("onnxruntime 스레드 제한 패치 적용 실패/스킵 (pip 미설치 등): %s", patch_err)


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
        self._last_high_time: float = 0.0
        self._last_mid_time: float = 0.0
        self._pcm_cache: "OrderedDict[str, bytes]" = OrderedDict()
        self._prerendered_cache = load_prerendered_cache(self._voice.config.sample_rate)
        self._stream = ResidentAudioStream(self._voice.config.sample_rate, device=device_idx)
        self._stream.start()

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
        self._stream.stop()

    def is_speaking(self) -> bool:
        """합성 중이거나 상주 스트림이 재생 중이면 True를 반환한다."""
        return (self._thread is not None and self._thread.is_alive()) or self._stream.is_playing()

    def _kill_current(self) -> None:
        """진행 중인 합성/재생을 즉시 중단시킨다.

        stop_flag를 세팅해 합성 루프가 이를 감지하도록 하고, 상주 스트림
        버퍼는 clear()로 즉시 비워 재생 중인 오디오를 끊는다. 재생 자체는
        스레드가 아닌 상주 스트림 콜백이 담당하므로 join은 합성 스레드
        종료 확인 용도로만 짧게 사용한다.

        clear() 대신 새 Event를 할당하여 이전/신규 스레드의 stop 신호를 격리한다.
        단, _speak_worker는 stop_flag를 self가 아닌 인자로 받으므로 교체 후에도
        이전 스레드가 올바른 Event를 참조한다.
        """
        if self._thread is not None and self._thread.is_alive():
            self._stop_flag.set()
            self._thread.join(timeout=0.5)
        self._stream.clear()
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
        """Piper로 PCM을 합성(또는 캐시 재사용)하고 상주 오디오 스트림으로 재생한다.

        config.TTS_PRERENDERED_PHRASES에 속한 고정 문구는 scripts/prerender_tts_cache.py로
        미리 렌더링된 WAV를 그대로 사용해 합성 자체를 건너뛴다. 그 외 문자열이
        반복 발화되는 경우 신경망 추론을 다시 수행하지 않도록 합성 결과를
        인메모리 LRU 캐시에 저장해 재사용한다.

        Args:
            text: 발화할 문자열.
            stop_flag: 이 스레드 전용 정지 플래그 (self._stop_flag와 독립).
        """
        prerendered_pcm = self._prerendered_cache.get(text)
        cached_pcm = self._pcm_cache.get(text)
        if prerendered_pcm is not None:
            pcm_bytes = prerendered_pcm
        elif cached_pcm is not None:
            self._pcm_cache.move_to_end(text)
            pcm_bytes = cached_pcm
        else:
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

            if stop_flag.is_set() or not pcm_bytes:
                return

            self._pcm_cache[text] = pcm_bytes
            if len(self._pcm_cache) > config.TTS_PCM_CACHE_MAX_ENTRIES:
                self._pcm_cache.popitem(last=False)

        if stop_flag.is_set() or not pcm_bytes:
            return

        # 상주 오디오 스트림(ResidentAudioStream) 버퍼에 채워 넣는다 — 매 발화마다
        # ALSA 디바이스를 열고 닫지 않으므로 즉시 반환하며 별도 폴링이 필요 없다.
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        stereo = np.stack([audio, audio], axis=-1)
        self._stream.play(stereo, interrupt=True)
