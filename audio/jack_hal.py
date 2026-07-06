"""3.5mm 이어폰 잭 오디오 HAL 구현체 (Orange Pi 5, ALSA)."""
import logging

import numpy as np

import config
from audio.hal import BaseAudioHAL
from fusion.engine import RiskLevel

logger = logging.getLogger(__name__)


class JackAudioHAL(BaseAudioHAL):
    """sounddevice를 사용해 3.5mm 잭으로 비프음을 출력하는 HAL 구현체.

    numpy로 사인파를 생성하고 sounddevice로 ALSA를 통해 비동기 재생한다.
    10ms fade-in/out 엔벨로프를 적용해 클릭 노이즈를 방지한다.

    BeepController가 호출 주기를 제어하므로, 이 클래스는 단순히
    play_alert() 호출당 하나의 비프음만 출력한다.
    """

    def __init__(self) -> None:
        self._sd = None
        self._device_idx: int | None = None
        self._running = False

    def start(self) -> None:
        """sounddevice를 초기화하고 ES8388(3.5mm 잭) 장치를 선택한다.

        Raises:
            RuntimeError: sounddevice 패키지 미설치 시.
        """
        try:
            import sounddevice as sd  # lazy import (Orange Pi 5 전용)

            self._sd = sd
        except ImportError as exc:
            raise RuntimeError(
                "sounddevice가 필요합니다: pip install sounddevice"
            ) from exc

        # ES8388 = 3.5mm 잭 장치를 명시적으로 선택 (기본값은 HDMI)
        devices = self._sd.query_devices()
        self._device_idx = next(
            (i for i, d in enumerate(devices)
             if "es8388" in d["name"].lower() and d["max_output_channels"] > 0),
            None,
        )
        if self._device_idx is None:
            logger.warning("ES8388 장치를 찾지 못했습니다 — 기본 장치로 대체합니다.")

        self._running = True
        logger.info(
            "JackAudioHAL 시작 (device=%s, sample_rate=%d Hz)",
            self._sd.query_devices(self._device_idx)["name"] if self._device_idx is not None else "default",
            config.AUDIO_SAMPLE_RATE,
        )

    def play_alert(self, risk_level: RiskLevel) -> None:
        """위험 수준에 맞는 비프음을 논블로킹으로 재생한다.

        Args:
            risk_level: 퓨전 엔진이 판단한 위험 수준.

        Raises:
            RuntimeError: start() 미호출 시.
        """
        if not self._running or self._sd is None:
            raise RuntimeError("start()를 먼저 호출하세요.")
        if risk_level == RiskLevel.NONE:
            return

        freq = (
            config.AUDIO_HIGH_FREQ_HZ
            if risk_level == RiskLevel.HIGH
            else config.AUDIO_MID_FREQ_HZ
        )
        duration_s = config.AUDIO_BEEP_DURATION_MS / 1000.0
        n_samples = int(config.AUDIO_SAMPLE_RATE * duration_s)

        t = np.linspace(0, duration_s, n_samples, endpoint=False)
        wave = np.sin(2 * np.pi * freq * t).astype(np.float32)

        # 클릭 방지: 10ms fade-in / fade-out 적용
        fade_samples = int(config.AUDIO_SAMPLE_RATE * 0.01)
        if fade_samples > 0 and n_samples >= fade_samples * 2:
            wave[:fade_samples] *= np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
            wave[-fade_samples:] *= np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)

        self._sd.play(wave, samplerate=config.AUDIO_SAMPLE_RATE, device=self._device_idx, blocking=False)
        logger.debug("비프음 재생: %s (%.0f Hz, %d ms)", risk_level.name, freq, config.AUDIO_BEEP_DURATION_MS)

    def play_occlusion_alert(self) -> None:
        """카메라 가림 경보음을 출력한다.

        800Hz 짧은 비프를 60ms 간격으로 3번 연속 출력해
        장애물 경보와 구분되는 패턴을 생성한다.

        Raises:
            RuntimeError: start() 미호출 시.
        """
        if not self._running or self._sd is None:
            raise RuntimeError("start()를 먼저 호출하세요.")

        freq = 800.0
        duration_s = 0.06  # 60ms
        gap_s = 0.06       # 비프 사이 무음 간격
        n_beep = int(config.AUDIO_SAMPLE_RATE * duration_s)
        n_gap = int(config.AUDIO_SAMPLE_RATE * gap_s)

        t = np.linspace(0, duration_s, n_beep, endpoint=False)
        beep = np.sin(2 * np.pi * freq * t).astype(np.float32)
        fade = int(config.AUDIO_SAMPLE_RATE * 0.01)
        if fade > 0 and n_beep >= fade * 2:
            beep[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
            beep[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)

        gap = np.zeros(n_gap, dtype=np.float32)
        wave = np.concatenate([beep, gap, beep, gap, beep])

        self._sd.play(wave, samplerate=config.AUDIO_SAMPLE_RATE, device=self._device_idx, blocking=False)
        logger.debug("카메라 가림 경보음 재생 (800Hz x3)")

    def stop(self) -> None:
        """재생 중인 오디오를 중단하고 리소스를 해제한다."""
        if self._running and self._sd is not None:
            self._sd.stop()
        self._running = False
        self._sd = None
        logger.info("JackAudioHAL 종료")
