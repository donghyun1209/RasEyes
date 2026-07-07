"""3.5mm 이어폰 잭 오디오 HAL 구현체 (Orange Pi 5, ALSA)."""
import logging
import subprocess

import numpy as np

import config
from audio.hal import BaseAudioHAL
from fusion.engine import RiskLevel

logger = logging.getLogger(__name__)


class JackAudioHAL(BaseAudioHAL):
    """aplay subprocess를 사용해 3.5mm 잭으로 비프음을 출력하는 HAL 구현체.

    numpy로 사인파를 생성하고 aplay를 통해 ALSA dmix로 재생한다.
    10ms fade-in/out 엔벨로프를 적용해 클릭 노이즈를 방지한다.
    PiperTts의 aplay와 동일한 경로를 사용하므로 dmix가 동시 재생을 처리한다.

    BeepController가 호출 주기를 제어하므로, 이 클래스는 단순히
    play_alert() 호출당 하나의 비프음만 출력한다.
    """

    def __init__(self) -> None:
        self._running = False

    def start(self) -> None:
        """ALSA aplay를 통한 오디오 출력을 초기화한다."""
        self._running = True
        logger.info("JackAudioHAL 시작 (device=default→ES8388 via aplay, sample_rate=%d Hz)", config.AUDIO_SAMPLE_RATE)

    def _play_wave(self, wave: np.ndarray) -> None:
        """float32 파형을 S16_LE PCM으로 변환해 aplay subprocess로 재생한다."""
        wave_int16 = (np.clip(wave, -1.0, 1.0) * 32767).astype(np.int16)
        try:
            proc = subprocess.Popen(
                ["aplay", "-D", "default", "-f", "S16_LE",
                 "-r", str(config.AUDIO_SAMPLE_RATE), "-c", "1", "--quiet"],
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            proc.stdin.write(wave_int16.tobytes())
            proc.stdin.close()
            proc.wait()
        except Exception as exc:
            logger.warning("비프음 재생 실패: %s", exc)

    def play_alert(self, risk_level: RiskLevel) -> None:
        """위험 수준에 맞는 비프음을 재생한다.

        Args:
            risk_level: 퓨전 엔진이 판단한 위험 수준.

        Raises:
            RuntimeError: start() 미호출 시.
        """
        if not self._running:
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

        self._play_wave(wave)
        logger.debug("비프음 재생: %s (%.0f Hz, %d ms)", risk_level.name, freq, config.AUDIO_BEEP_DURATION_MS)

    def play_occlusion_alert(self) -> None:
        """카메라 가림 경보음을 출력한다.

        800Hz 짧은 비프를 60ms 간격으로 3번 연속 출력해
        장애물 경보와 구분되는 패턴을 생성한다.

        Raises:
            RuntimeError: start() 미호출 시.
        """
        if not self._running:
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

        self._play_wave(wave)
        logger.debug("카메라 가림 경보음 재생 (800Hz x3)")

    def stop(self) -> None:
        """리소스를 해제한다."""
        self._running = False
        logger.info("JackAudioHAL 종료")
