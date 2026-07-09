"""EspeakTts — espeak-ng 기반 비동기 TTS 구현체."""
import io
import logging
import subprocess
import threading
import time
import wave
from typing import Optional

import numpy as np

import config
from audio.interface import BaseTtsHAL
from audio.resident_stream import ResidentAudioStream
from fusion.engine import RiskLevel

logger = logging.getLogger(__name__)


class EspeakTts(BaseTtsHAL):
    """espeak-ng --stdout PCM을 sounddevice로 재생하는 TTS 구현체.

    espeak-ng가 직접 ALSA를 열지 않고 stdout으로 WAV를 출력하면,
    sounddevice가 이를 재생하여 JackAudioHAL과의 장치 충돌을 방지한다.

    HIGH/MID 쿨다운을 독립적으로 관리한다. HIGH 발화가 들어오면 진행 중인
    스레드를 중단하고 즉시 재발화한다 (HIGH 우선). MID는 발화 중이면 skip한다.

    사전 조건: `sudo apt install espeak-ng`
    """

    def __init__(self, device_idx: Optional[int] = None) -> None:
        self._device_idx = device_idx
        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._last_high_time: float = 0.0
        self._last_mid_time: float = 0.0
        self._stream = ResidentAudioStream(config.AUDIO_SAMPLE_RATE, device=device_idx)
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
        """진행 중인 합성/재생에 중단 신호를 보내고 즉시 복귀한다.

        clear() 대신 새 Event를 할당하여 이전 스레드와 신규 스레드의
        정지 신호를 격리한다 (레이스 컨디션 방지). 상주 스트림 버퍼는
        clear()로 즉시 비워 재생 중인 오디오를 끊는다.
        join timeout을 0.1s로 제한하여 E2E latency KPI를 보호한다.
        """
        if self._thread is not None and self._thread.is_alive():
            self._stop_flag.set()
            self._thread.join(timeout=0.1)
        self._stream.clear()
        self._stop_flag = threading.Event()
        self._thread = None

    def _start_thread(self, text: str) -> None:
        """발화 스레드를 비동기로 시작한다."""
        stop_flag = self._stop_flag  # 현재 Event 캡처 — 교체 후에도 유효
        self._thread = threading.Thread(
            target=self._speak_worker,
            args=(text, stop_flag),
            daemon=True,
            name="tts-worker",
        )
        self._thread.start()

    def _speak_worker(self, text: str, stop_flag: threading.Event) -> None:
        """espeak-ng --stdout으로 WAV를 생성하고 상주 오디오 스트림으로 재생한다."""
        try:
            result = subprocess.run(
                [
                    "espeak-ng",
                    "-v", config.TTS_ESPEAK_VOICE,
                    "-s", str(config.TTS_ESPEAK_RATE),
                    "--stdout",
                    text,
                ],
                capture_output=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.warning("espeak-ng 실행 실패: %s", exc)
            return

        if result.returncode != 0 or not result.stdout:
            logger.warning("espeak-ng 비정상 종료 (rc=%d)", result.returncode)
            return

        if stop_flag.is_set():
            return

        try:
            with wave.open(io.BytesIO(result.stdout)) as wf:
                pcm = wf.readframes(wf.getnframes())
                src_rate = wf.getframerate()
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if len(audio) == 0:
                return
            # JackAudioHAL과 동일한 샘플레이트·채널로 맞춰 ALSA 스트림 재구성 방지
            if src_rate != config.AUDIO_SAMPLE_RATE and config.AUDIO_SAMPLE_RATE % src_rate == 0:
                audio = np.repeat(audio, config.AUDIO_SAMPLE_RATE // src_rate)
            if audio.ndim == 1:
                audio = np.stack([audio, audio], axis=-1)  # mono → stereo
            if stop_flag.is_set():
                return
            self._stream.play(audio, interrupt=True)
        except Exception as exc:
            logger.warning("TTS 재생 실패: %s", exc)
