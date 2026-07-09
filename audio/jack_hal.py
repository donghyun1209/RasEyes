"""3.5mm 이어폰 잭 오디오 HAL 구현체 (Orange Pi 5, ALSA)."""
import logging
import subprocess
import time

import numpy as np

import config
from audio.interface import BaseAudioHAL
from audio.resident_stream import ResidentAudioStream
from fusion.engine import RiskLevel

logger = logging.getLogger(__name__)


class JackAudioHAL(BaseAudioHAL):
    """sounddevice를 사용해 3.5mm 잭으로 비프음을 출력하는 HAL 구현체.

    numpy로 사인파를 생성해 ALSA dmix로 재생한다.
    10ms fade-in/out 엔벨로프를 적용해 클릭 노이즈를 방지한다.
    매 재생마다 subprocess를 새로 포크하지 않아(aplay 대비) CPU 오버헤드를 줄인다.

    BeepController가 호출 주기를 제어하므로, 이 클래스는 단순히
    play_alert() 호출당 하나의 비프음만 출력한다.

    상주 sd.OutputStream(ResidentAudioStream)에 PCM을 채워 넣는 방식으로 재생하여
    호출 즉시 반환한다(논블로킹). 매 재생마다 ALSA 디바이스를 열고 닫지 않으므로
    코덱/앰프의 반복적인 전원 온오프로 인한 전류 스파이크를 방지한다.
    """

    def __init__(self) -> None:
        self._running = False
        self._beep_waves: dict = {
            RiskLevel.HIGH: self._build_beep_wave(config.AUDIO_HIGH_FREQ_HZ),
            RiskLevel.MID: self._build_beep_wave(config.AUDIO_MID_FREQ_HZ),
        }
        self._occlusion_wave = self._build_occlusion_wave()
        self._stream = ResidentAudioStream(config.AUDIO_SAMPLE_RATE)

    @staticmethod
    def _build_beep_wave(freq: float) -> np.ndarray:
        """지정 주파수의 비프음 파형을 lead-in 무음 구간과 함께 1회 생성한다.

        Args:
            freq: 비프음 주파수 (Hz).

        Returns:
            fade-in/out과 lead-in 무음이 적용된 float32 모노 파형.
        """
        duration_s = config.AUDIO_BEEP_DURATION_MS / 1000.0
        n_samples = int(config.AUDIO_SAMPLE_RATE * duration_s)

        t = np.linspace(0, duration_s, n_samples, endpoint=False)
        wave = config.AUDIO_BEEP_VOLUME * np.sin(2 * np.pi * freq * t).astype(np.float32)

        fade_samples = int(config.AUDIO_SAMPLE_RATE * config.AUDIO_FADE_MS / 1000.0)
        if fade_samples > 0 and n_samples >= fade_samples * 2:
            wave[:fade_samples] *= np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
            wave[-fade_samples:] *= np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)

        leadin = np.zeros(int(config.AUDIO_SAMPLE_RATE * config.AUDIO_BEEP_LEADIN_MS / 1000.0), dtype=np.float32)
        return np.concatenate([leadin, wave])

    @staticmethod
    def _build_occlusion_wave() -> np.ndarray:
        """카메라 가림 경보음(짧은 비프 3연타) 파형을 1회 생성한다.

        Returns:
            fade-in/out과 lead-in 무음이 적용된 float32 모노 파형.
        """
        freq = config.CAMERA_OCCLUSION_ALERT_FREQ_HZ
        duration_s = config.CAMERA_OCCLUSION_ALERT_BEEP_MS / 1000.0
        gap_s = config.CAMERA_OCCLUSION_ALERT_GAP_MS / 1000.0
        n_beep = int(config.AUDIO_SAMPLE_RATE * duration_s)
        n_gap = int(config.AUDIO_SAMPLE_RATE * gap_s)

        t = np.linspace(0, duration_s, n_beep, endpoint=False)
        beep = config.AUDIO_BEEP_VOLUME * np.sin(2 * np.pi * freq * t).astype(np.float32)
        fade = int(config.AUDIO_SAMPLE_RATE * config.AUDIO_FADE_MS / 1000.0)
        if fade > 0 and n_beep >= fade * 2:
            beep[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
            beep[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)

        gap = np.zeros(n_gap, dtype=np.float32)
        leadin = np.zeros(int(config.AUDIO_SAMPLE_RATE * config.AUDIO_BEEP_LEADIN_MS / 1000.0), dtype=np.float32)
        return np.concatenate([leadin, beep, gap, beep, gap, beep])

    def start(self) -> None:
        """ALSA aplay를 통한 오디오 출력을 초기화한다.

        부팅/오디오 카드 상태 변경 시 hp switch·spk switch가 음소거(off)로
        재설정되는 경우가 있어, 기동 시마다 강제로 음소거를 해제하는
        안전장치를 적용한다.
        """
        self._running = True
        self._unmute_hw()
        self._stream.start()
        logger.info("JackAudioHAL 시작 (device=default→ES8388 via aplay, sample_rate=%d Hz)", config.AUDIO_SAMPLE_RATE)

    def _unmute_hw(self) -> None:
        """ES8388 코덱(card config.ALSA_CARD_INDEX)의 출력 스위치 음소거를 강제 해제한다.

        기동 직후 ALSA 드라이버 상태에 따라 amixer 설정이 일시적으로 무시될 수
        있으므로, 설정 후 값을 재조회해 실제로 반영되었는지 검증하고 실패 시
        짧은 지연 후 재시도한다. 최대 재시도 후에도 실패하면 경고 로그만 남기고
        진행한다 (오디오 출력 자체는 시도되므로 완전히 막지 않는다).
        """
        for attempt in range(1, config.AUDIO_UNMUTE_MAX_RETRIES + 1):
            for cmd in (
                ["amixer", "-c", config.ALSA_CARD_INDEX, "sset", "hp switch", "on"],
                ["amixer", "-c", config.ALSA_CARD_INDEX, "sset", "spk switch", "on"],
                ["amixer", "-c", config.ALSA_CARD_INDEX, "sset", "PCM", config.ALSA_PCM_VOLUME],
            ):
                try:
                    subprocess.run(cmd, capture_output=True, timeout=2)
                except Exception as exc:
                    logger.warning("amixer 음소거 해제 실패 (%s): %s", " ".join(cmd), exc)

            if self._verify_unmuted():
                return
            logger.warning(
                "믹서 상태 검증 실패 (%d/%d), %.1fs 후 재시도",
                attempt,
                config.AUDIO_UNMUTE_MAX_RETRIES,
                config.AUDIO_UNMUTE_RETRY_DELAY_SEC,
            )
            time.sleep(config.AUDIO_UNMUTE_RETRY_DELAY_SEC)

        logger.error("믹서 음소거 해제 검증 최종 실패 — 오디오 출력이 음소거 상태일 수 있습니다.")

    def _verify_unmuted(self) -> bool:
        """hp switch·spk switch가 실제로 on 상태인지 amixer 조회로 확인한다.

        Returns:
            두 스위치 모두 on이면 True, 조회 실패 또는 off면 False.
        """
        try:
            for control in ("hp switch", "spk switch"):
                result = subprocess.run(
                    ["amixer", "-c", config.ALSA_CARD_INDEX, "sget", control],
                    capture_output=True,
                    timeout=2,
                    text=True,
                )
                if "[on]" not in result.stdout:
                    return False
            return True
        except Exception as exc:
            logger.warning("믹서 상태 조회 실패: %s", exc)
            return False

    def _play_wave(self, wave: np.ndarray) -> None:
        """float32 모노 파형을 상주 오디오 스트림 버퍼에 채워 넣는다.

        ~/.asoundrc의 dmix 슬레이브가 channels 2로 고정되어 있어, 모노로
        전송 시 ALSA plug 레이어의 채널 변환 과정에서 왜곡이 생길 수 있다.
        L/R 채널을 동일하게 복제한 스테레오로 전송해 변환 없이 그대로 흘려보낸다.

        ResidentAudioStream.play()는 버퍼에 데이터를 채워 넣기만 하고 즉시
        반환하므로(논블로킹), 별도 스레드 없이 호출해도 메인 흐름을 막지 않는다.
        """
        stereo = np.stack([np.clip(wave, -1.0, 1.0)] * 2, axis=-1)
        self._stream.play(stereo)

    def play_alert(self, risk_level: RiskLevel) -> None:
        """위험 수준에 맞는 비프음을 재생한다 (논블로킹, 즉시 반환).

        Args:
            risk_level: 퓨전 엔진이 판단한 위험 수준.

        Raises:
            RuntimeError: start() 미호출 시.
        """
        if not self._running:
            raise RuntimeError("start()를 먼저 호출하세요.")
        if risk_level == RiskLevel.NONE:
            return

        wave = self._beep_waves[risk_level]
        self._play_wave(wave)
        logger.debug("비프음 재생: %s (%d ms)", risk_level.name, config.AUDIO_BEEP_DURATION_MS)

    def play_occlusion_alert(self) -> None:
        """카메라 가림 경보음을 출력한다 (논블로킹, 즉시 반환).

        800Hz 짧은 비프를 60ms 간격으로 3번 연속 출력해
        장애물 경보와 구분되는 패턴을 생성한다.

        Raises:
            RuntimeError: start() 미호출 시.
        """
        if not self._running:
            raise RuntimeError("start()를 먼저 호출하세요.")

        self._play_wave(self._occlusion_wave)
        logger.debug("카메라 가림 경보음 재생 (%.0fHz x3)", config.CAMERA_OCCLUSION_ALERT_FREQ_HZ)

    def stop(self) -> None:
        """리소스를 해제한다."""
        self._running = False
        self._stream.stop()
        logger.info("JackAudioHAL 종료")
