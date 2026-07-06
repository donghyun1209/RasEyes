"""VL53L1X ToF 센서 HAL 구현체 (Orange Pi 5, i2c-5)."""
import logging
import threading

import config
from sensor.hal import BaseToFHAL

logger = logging.getLogger(__name__)


class VL53L1XHAL(BaseToFHAL):
    """pimoroni vl53l1x 라이브러리를 사용하는 ToF 센서 HAL 구현체.

    Orange Pi 5에서 I2C5_M3(i2c-5)로 연결된 VL53L1X(주소 0x29)를 제어한다.
    64비트 aarch64 환경의 ctypes 버그를 start()에서 자동 수정한다.

    Ranging mode: MEDIUM(2) — 최대 3m, 최소 33ms 타이밍 버짓.
    (LONG(3)은 최소 140ms 필요; 50ms 설정 시 측정 주기가 1s 이상으로 늘어 데이터 만료 발생)

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
        from ctypes import c_int, c_uint, c_uint16, c_void_p

        lib = VL53L1X._TOF_LIBRARY  # _TOF_LIBRARY is a module-level variable, not a class attr
        lib.initialise.restype = c_void_p
        lib.startRanging.argtypes = [c_void_p, c_int]
        lib.stopRanging.argtypes = [c_void_p]
        lib.getDistance.argtypes = [c_void_p]
        lib.getDistance.restype = c_uint16
        lib.setMeasurementTimingBudgetMicroSeconds.argtypes = [c_void_p, c_uint]
        lib.setInterMeasurementPeriodMilliSeconds.argtypes = [c_void_p, c_uint]

        try:
            self._tof = VL53L1X.VL53L1X(i2c_bus=self._i2c_port)
            self._tof.open()
            self._tof.set_timing(
                self._timing_budget_us, self._inter_measurement_ms
            )
            self._tof.start_ranging(2)  # 2 = MEDIUM (최대 3m, 200ms+ 타이밍 버짓 필요)
        except Exception as exc:
            if self._tof is not None:
                try:
                    self._tof.close()
                except Exception:
                    pass
            self._tof = None
            raise RuntimeError(f"VL53L1X 초기화 실패: {exc}") from exc

        self._running = True
        logger.info(
            "VL53L1XHAL 시작 (i2c-%d, timing=%dµs, interval=%dms)",
            self._i2c_port,
            self._timing_budget_us,
            self._inter_measurement_ms,
        )

    def read_distance_cm(self) -> float:
        """현재 거리 측정값을 cm 단위로 반환한다.

        get_distance()가 하드웨어 이슈로 무한 블로킹하는 경우를 방지하기 위해
        1초 timeout을 적용한다. timeout 시 RuntimeError를 발생시켜 워커가 재시도한다.

        Returns:
            측정된 거리 (cm). 범위 초과 시 TOF_OUT_OF_RANGE_CM.

        Raises:
            RuntimeError: start() 미호출 또는 get_distance() timeout 시.
        """
        if not self._running or self._tof is None:
            raise RuntimeError("start()를 먼저 호출하세요.")

        result: list = [None]

        def _read() -> None:
            result[0] = self._tof.get_distance()

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout=1.0)
        if result[0] is None:
            raise RuntimeError("get_distance() 1초 timeout — 센서 무응답")

        distance_mm = result[0]
        if distance_mm == 0:
            return config.TOF_OUT_OF_RANGE_CM
        return distance_mm / 10.0  # mm → cm

    def stop(self) -> None:
        """측정을 중단하고 센서 리소스를 해제한다."""
        if self._tof is not None and self._running:
            try:
                self._tof.stop_ranging()
                self._tof.close()
            except Exception as exc:
                logger.warning("VL53L1XHAL 정리 중 오류 (무시): %s", exc)
            self._tof = None
        self._running = False
        logger.info("VL53L1XHAL 종료")
