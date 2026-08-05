"""VL53L1X ToF 센서 HAL 구현체 (Orange Pi 5, i2c-5)."""
import logging
import threading
import time
from typing import Optional

import config
from sensor.interface import BaseToFHAL

logger = logging.getLogger(__name__)


class VL53L1XHAL(BaseToFHAL):
    """pimoroni vl53l1x 라이브러리를 사용하는 ToF 센서 HAL 구현체.

    Orange Pi 5에서 I2C5_M3(i2c-5)로 연결된 VL53L1X(주소 0x29)를 제어한다.
    64비트 aarch64 환경의 ctypes 버그를 start()에서 자동 수정한다.

    Ranging mode: MEDIUM(2) — 최대 3m, 최소 33ms 타이밍 버짓.
    (LONG(3)은 최소 140ms 필요; 50ms 설정 시 측정 주기가 1s 이상으로 늘어 데이터 만료 발생)

    `config.TOF_ROI_ENABLED`이면 SPAD 격자 일부만 쓰도록 시야를 제한한다(_apply_roi).

    Args:
        i2c_port: I2C 버스 번호 (Orange Pi 5 기본값: 5).
        timing_budget_us: 측정 타이밍 버짓 (마이크로초).
        inter_measurement_ms: 측정 간격 (밀리초).
    """

    def __init__(
        self,
        i2c_port: int = config.TOF_I2C_PORT,
        timing_budget_us: int = config.TOF_TIMING_BUDGET_US,
        inter_measurement_ms: int = config.TOF_INTER_MEASUREMENT_MS,
    ) -> None:
        self._i2c_port = i2c_port
        self._timing_budget_us = timing_budget_us
        self._inter_measurement_ms = inter_measurement_ms
        self._tof = None
        self._running = False
        self._lock = threading.Lock()
        self._latest_distance_mm: Optional[int] = None
        self._latest_update_ts: float = 0.0
        self._sample_seq: int = 0
        self._poll_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """센서를 초기화하고 측정을 시작한다.

        aarch64 64비트 환경에서 pimoroni vl53l1x 라이브러리의 ctypes 타입이
        잘못 추론되어 segfault가 발생하는 버그를 패치한다.

        Raises:
            RuntimeError: VL53L1X 패키지 미설치 또는 센서 초기화 실패 시.
        """
        try:
            import VL53L1X  # noqa: N813  # lazy import (Orange Pi 5 전용)
        except ImportError as exc:
            raise RuntimeError(
                "VL53L1X 패키지가 필요합니다: pip install VL53L1X"
            ) from exc

        # aarch64 ctypes 버그 수정 — initialise/startRanging/getDistance 등의
        # argtypes·restype이 32비트 기준으로 설정되어 64비트에서 segfault 발생
        from ctypes import c_int, c_uint, c_uint8, c_uint16, c_void_p

        lib = VL53L1X._TOF_LIBRARY  # _TOF_LIBRARY is a module-level variable, not a class attr
        lib.initialise.restype = c_void_p
        lib.startRanging.argtypes = [c_void_p, c_int]
        lib.stopRanging.argtypes = [c_void_p]
        lib.getDistance.argtypes = [c_void_p]
        lib.getDistance.restype = c_uint16
        lib.setMeasurementTimingBudgetMicroSeconds.argtypes = [c_void_p, c_uint]
        lib.setInterMeasurementPeriodMilliSeconds.argtypes = [c_void_p, c_uint]
        lib.setUserRoi.argtypes = [c_void_p, c_uint8, c_uint8, c_uint8, c_uint8]

        try:
            self._tof = VL53L1X.VL53L1X(i2c_bus=self._i2c_port)
            self._tof.open()
            self._tof.set_timing(
                self._timing_budget_us, self._inter_measurement_ms
            )
            if config.TOF_ROI_ENABLED:
                self._apply_roi(VL53L1X)
            self._tof.start_ranging(config.TOF_RANGING_MODE_MEDIUM)
        except Exception as exc:
            if self._tof is not None:
                try:
                    self._tof.close()
                except Exception:
                    pass
            self._tof = None
            raise RuntimeError(f"VL53L1X 초기화 실패: {exc}") from exc

        self._running = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="tof-poll"
        )
        self._poll_thread.start()
        logger.info(
            "VL53L1XHAL 시작 (i2c-%d, timing=%dµs, interval=%dms)",
            self._i2c_port,
            self._timing_budget_us,
            self._inter_measurement_ms,
        )

    def _apply_roi(self, vl53l1x_module) -> None:
        """SPAD 격자의 일부만 쓰도록 시야(ROI)를 제한한다.

        가슴 높이 착용 시 27° FoV의 아래쪽 끄트머리에 지면이 걸려, 2026-08-04 야외
        로그에서 유효 측정의 92%가 100~124cm 한 구간에 몰렸다. 격자의 절반만 쓰면
        물리 가림막 없이 그 방향을 잘라낼 수 있다.

        실패해도 예외를 올리지 않는다 — 시야가 넓은 것보다 센서가 아예 없는 쪽이
        훨씬 나쁘다. 경고만 남기고 전체 FoV로 계속 진행한다.

        Args:
            vl53l1x_module: start()에서 지연 임포트한 VL53L1X 모듈.
        """
        try:
            # 클래스명은 소문자 x다 (VL53L1XUserRoi는 존재하지 않는다)
            roi = vl53l1x_module.VL53L1xUserRoi(
                config.TOF_ROI_TOP_LEFT_X,
                config.TOF_ROI_TOP_LEFT_Y,
                config.TOF_ROI_BOT_RIGHT_X,
                config.TOF_ROI_BOT_RIGHT_Y,
            )
            self._tof.set_user_roi(roi)
            logger.info(
                "ToF ROI 적용: (%d,%d)-(%d,%d)",
                config.TOF_ROI_TOP_LEFT_X,
                config.TOF_ROI_TOP_LEFT_Y,
                config.TOF_ROI_BOT_RIGHT_X,
                config.TOF_ROI_BOT_RIGHT_Y,
            )
        except Exception as exc:
            logger.warning("ToF ROI 적용 실패, 전체 FoV로 계속: %s", exc)

    def _poll_loop(self) -> None:
        """단일 상주 스레드에서 센서 값을 지속적으로 읽어 최신값을 갱신한다.

        매 호출마다 스레드를 새로 생성하던 기존 방식은 I2C 버스 락업 시
        스레드가 영원히 반환되지 않아 초당 15개씩 누적되는 Thread Leak을
        유발했다. 상주 스레드 1개만 블로킹시키고, read_distance_cm()은
        공유 변수의 최신값만 즉시 반환하도록 분리한다.
        """
        while self._running:
            try:
                distance_mm = self._tof.get_distance()
                with self._lock:
                    self._latest_distance_mm = distance_mm
                    self._latest_update_ts = time.monotonic()
                    self._sample_seq += 1
                time.sleep(config.TOF_POLL_INTERVAL_SEC)
            except Exception as exc:
                logger.warning("ToF 폴링 오류 (무시하고 계속): %s", exc)
                time.sleep(config.TOF_POLL_INTERVAL_SEC)

    @property
    def sample_seq(self) -> int:
        """폴링 루프가 값을 갱신할 때마다 증가하는 카운터.

        폴링 주기(TOF_POLL_INTERVAL_SEC=0.20s)와 센서 측정 주기
        (TOF_INTER_MEASUREMENT_MS=210ms)가 어긋나 드리프트가 있으므로 "시퀀스 증가 =
        새 물리 측정"이 엄밀히 보장되지는 않는다. 다만 메인 루프(15Hz)가 같은 값을
        3번씩 읽던 3배 과샘플링을 ~1.05배로 줄이는 것이 목적이라 충분하다.

        Returns:
            단조 증가하는 샘플 시퀀스 번호.
        """
        with self._lock:
            return self._sample_seq

    def read_distance_cm(self) -> float:
        """가장 최근에 폴링된 거리 측정값을 cm 단위로 반환한다.

        백그라운드 폴링 스레드가 갱신하는 공유 변수를 즉시 반환하므로
        블로킹이 없다. 값이 1초 이상 갱신되지 않으면 센서 무응답으로 간주한다.

        Returns:
            측정된 거리 (cm). 범위 초과 또는 무효 측정(물리적 최소 거리
            TOF_MIN_VALID_CM 미만 — 직사광 포화 시 0.1~1cm 쓰레기값) 시
            TOF_OUT_OF_RANGE_CM.

        Raises:
            RuntimeError: start() 미호출 또는 1초 이상 값 갱신이 없을 시.
        """
        if not self._running or self._tof is None:
            raise RuntimeError("start()를 먼저 호출하세요.")

        with self._lock:
            distance_mm = self._latest_distance_mm
            update_ts = self._latest_update_ts

        if distance_mm is None or time.monotonic() - update_ts > config.TOF_STALE_TIMEOUT_SEC:
            raise RuntimeError("ToF 센서 1초 이상 무응답")

        # 0(측정 실패)과 물리적 최소 거리 미만의 쓰레기값(직사광 포화 시 1mm 등)은
        # 모두 "신뢰할 측정 없음" — 이동평균에 넣으면 안전 방향이 아니라 근접
        # 방향으로 평균을 왜곡해 오경보가 된다
        if distance_mm < config.TOF_MIN_VALID_CM * 10:
            return config.TOF_OUT_OF_RANGE_CM
        return distance_mm / 10.0  # mm → cm

    def stop(self) -> None:
        """측정을 중단하고 센서 리소스를 해제한다."""
        self._running = False
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=1.0)
            self._poll_thread = None
        if self._tof is not None:
            try:
                self._tof.stop_ranging()
                self._tof.close()
            except Exception as exc:
                logger.warning("VL53L1XHAL 정리 중 오류 (무시): %s", exc)
            self._tof = None
        logger.info("VL53L1XHAL 종료")
