"""VL53L1X ToF 센서 HAL 구현체 (Orange Pi 5, i2c-5)."""
import logging
import threading
import time
from ctypes import (POINTER, Structure, byref, c_int16, c_uint8, c_uint16,
                    c_uint32, c_void_p)
from typing import Optional

import config
from sensor.interface import BaseToFHAL

logger = logging.getLogger(__name__)


class _RangingMeasurementData(Structure):
    """ST `VL53L1_RangingMeasurementData_t`의 ctypes 대응 구조체 (vl53l1_def.h).

    pimoroni 래퍼의 `getDistance()`는 이 구조체를 채운 뒤 거리만 반환하고
    RangeStatus·신호 강도를 버린다. 그 정보가 있어야 "표적 없음"과 "유효 측정"을
    구분할 수 있어서(2026-08-08 실측), 원본 API를 직접 호출해 통째로 받는다.

    `FixPoint1616_t`는 uint32다. 네이티브 정렬 기준 sizeof == 28이며, Pi 실측으로
    확인했다 — 오프셋이 틀리면 status가 조용히 쓰레기값이 되므로 구조를 바꾸지 말 것.
    """

    _fields_ = [
        ("TimeStamp", c_uint32),
        ("StreamCount", c_uint8),
        ("RangeQualityLevel", c_uint8),
        ("SignalRateRtnMegaCps", c_uint32),
        ("AmbientRateRtnMegaCps", c_uint32),
        ("EffectiveSpadRtnCount", c_uint16),
        ("SigmaMilliMeter", c_uint32),
        ("RangeMilliMeter", c_int16),
        ("RangeFractionalPart", c_uint8),
        ("RangeStatus", c_uint8),
    ]


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
        # RangeStatus 게이트 (config.TOF_STATUS_GATE_ENABLED). 조회에 실패하면
        # 런타임에 꺼지고 거리 값만으로 계속한다.
        self._status_gate: bool = config.TOF_STATUS_GATE_ENABLED
        self._latest_status: Optional[int] = None
        self._lib = None
        self._meas = _RangingMeasurementData()

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
        from ctypes import c_int, c_int8, c_uint

        lib = VL53L1X._TOF_LIBRARY  # _TOF_LIBRARY is a module-level variable, not a class attr
        lib.initialise.restype = c_void_p
        lib.startRanging.argtypes = [c_void_p, c_int]
        lib.stopRanging.argtypes = [c_void_p]
        lib.getDistance.argtypes = [c_void_p]
        lib.getDistance.restype = c_uint16
        lib.setMeasurementTimingBudgetMicroSeconds.argtypes = [c_void_p, c_uint]
        lib.setInterMeasurementPeriodMilliSeconds.argtypes = [c_void_p, c_uint]
        lib.setUserRoi.argtypes = [c_void_p, c_uint8, c_uint8, c_uint8, c_uint8]
        # 래퍼가 노출하지 않는 ST 원본 API — RangeStatus를 읽는 유일한 경로
        lib.VL53L1_GetRangingMeasurementData.argtypes = [
            c_void_p, POINTER(_RangingMeasurementData)
        ]
        lib.VL53L1_GetRangingMeasurementData.restype = c_int8  # VL53L1_Error
        self._lib = lib

        # ⚠️ 재초기화 경로에서 반드시 먼저 호출한다. 이게 없으면 옛 폴링 스레드가
        # 살아 있는 채로 아래에서 self._tof가 교체되고, 그 스레드가 아직 open()
        # 전인 객체의 _dev(=None)를 원본 API에 넘겨 NULL 역참조로 죽는다.
        # ctypes는 argtypes가 c_void_p면 None을 NULL로 조용히 통과시키므로
        # _read_status의 try/except로도 안 잡힌다 (2026-08-09 야외 실측:
        # I2C 접촉 불량 → 재초기화 → SEGV로 서비스가 19회 재시작).
        self._stop_polling()

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

    def _stop_polling(self) -> None:
        """폴링 스레드를 정지시키고 합류를 기다린 뒤 이전 센서 핸들을 닫는다.

        `start()`가 재초기화로 다시 불릴 때 옛 스레드와 새 스레드가 같은
        `self._tof`를 두고 경쟁하는 것을 막는다. 첫 `start()`에서는 스레드가 없어
        아무 일도 하지 않는다.

        Raises:
            RuntimeError: 폴링 스레드가 제한 시간 안에 정지하지 않은 경우.
                이때는 `self._tof`를 건드리지 않고 예외를 올린다 — 살아 있는
                스레드가 보는 객체를 교체하느니 재초기화를 실패시키는 편이 낫다
                (호출자가 재시도하고, 끝내 실패하면 ToF만 죽고 비전은 계속 돈다).
        """
        self._running = False
        thread = self._poll_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=config.TOF_POLL_JOIN_TIMEOUT_SEC)
            if thread.is_alive():
                raise RuntimeError(
                    "ToF 폴링 스레드가 "
                    f"{config.TOF_POLL_JOIN_TIMEOUT_SEC}초 안에 정지하지 않았다"
                )
        self._poll_thread = None
        if self._tof is not None:
            try:
                self._tof.stop_ranging()
                self._tof.close()
            except Exception as exc:
                logger.warning("이전 ToF 핸들 정리 중 오류 (무시): %s", exc)
            self._tof = None

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
                status = self._read_status()
                if status is not None:
                    # 거리도 구조체 값으로 대체한다. get_distance()가 돌려준 값과
                    # 구조체 값은 서로 다른 측정일 수 있어(2026-08-08 야외 실측:
                    # 값이 튀는 구간에서 최대 91% 불일치), 둘을 섞으면 한 측정의
                    # status가 다른 측정의 거리를 판정하게 된다.
                    distance_mm = self._meas.RangeMilliMeter
                with self._lock:
                    self._latest_distance_mm = distance_mm
                    self._latest_status = status
                    self._latest_update_ts = time.monotonic()
                    self._sample_seq += 1
                time.sleep(config.TOF_POLL_INTERVAL_SEC)
            except Exception as exc:
                logger.warning("ToF 폴링 오류 (무시하고 계속): %s", exc)
                time.sleep(config.TOF_POLL_INTERVAL_SEC)

    def _read_status(self) -> Optional[int]:
        """직전 측정의 RangeStatus를 읽는다 (`self._meas`도 함께 채워진다).

        pimoroni 래퍼에는 이 값을 얻을 API가 없어 ST 원본 함수를 직접 호출한다.
        핸들(`_tof._dev`)은 래퍼가 private으로 들고 있지만, 래퍼 자신도 이 핸들을
        원본 API에 그대로 넘기고 있어(VL53L1X.py:223) 타입이 통용된다.

        조회에 실패하면 게이트를 끄고 이후로는 시도하지 않는다 — 매 폴링마다
        경고를 쌓는 것보다, 거리 값만으로 계속 도는 쪽이 낫다 (_apply_roi와 같은
        원칙: 시야가 넓은 것보다 센서가 아예 없는 쪽이 훨씬 나쁘다).

        Returns:
            RangeStatus (0=RANGE_VALID). 게이트가 꺼져 있거나 조회 실패 시 None.
        """
        if not self._status_gate or self._lib is None:
            return None
        # NULL 역참조 방어. ctypes는 argtypes가 c_void_p면 None을 NULL로 조용히
        # 넘기고, C 라이브러리가 그걸 역참조하면 파이썬 예외가 아니라 SEGV다 —
        # 아래 try/except로는 절대 못 잡는다. 재초기화 경합은 _stop_polling()이
        # 막지만, 이 호출은 실패 비용이 프로세스 종료라 한 겹 더 둔다.
        dev = getattr(self._tof, "_dev", None)
        if not dev:
            return None
        try:
            err = self._lib.VL53L1_GetRangingMeasurementData(
                dev, byref(self._meas)
            )
        except Exception as exc:  # ctypes 호출 자체가 실패한 경우
            logger.warning("RangeStatus 조회 예외 — 게이트를 끄고 계속: %s", exc)
            self._status_gate = False
            return None
        if err != 0:
            logger.warning(
                "RangeStatus 조회 실패(VL53L1_Error=%d) — 게이트를 끄고 "
                "거리 값만으로 계속한다. 표적 없는 방향의 허수값을 "
                "걸러내지 못하게 되므로 오경보가 늘 수 있다.", err
            )
            self._status_gate = False
            return None
        return self._meas.RangeStatus

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
            측정된 거리 (cm). 다음 중 하나라도 해당하면 TOF_OUT_OF_RANGE_CM:
            RangeStatus가 무효, 범위 초과, 물리적 최소 거리 TOF_MIN_VALID_CM
            미만(직사광 포화 시 0.1~1cm 쓰레기값).

        Raises:
            RuntimeError: start() 미호출 또는 1초 이상 값 갱신이 없을 시.
        """
        if not self._running or self._tof is None:
            raise RuntimeError("start()를 먼저 호출하세요.")

        with self._lock:
            distance_mm = self._latest_distance_mm
            update_ts = self._latest_update_ts
            status = self._latest_status

        if distance_mm is None or time.monotonic() - update_ts > config.TOF_STALE_TIMEOUT_SEC:
            raise RuntimeError("ToF 센서 1초 이상 무응답")

        # RangeStatus가 무효면 거리값 자체가 허수다 — 크기로는 절대 못 거른다.
        # 2026-08-08 야외 실측: 표적이 없는 트인 공간에서 508샘플 전부 무효였는데
        # 거리는 34~321cm(중앙값 120.8cm)로 그럴듯했고, 그 대역이 경보 임계값
        # (100/150cm) 한복판이라 빈 공간에서 경보가 계속 나갔다.
        if status is not None and status not in config.TOF_VALID_RANGE_STATUS:
            return config.TOF_OUT_OF_RANGE_CM

        # 0(측정 실패)과 물리적 최소 거리 미만의 쓰레기값(직사광 포화 시 1mm 등)은
        # 모두 "신뢰할 측정 없음" — 이동평균에 넣으면 안전 방향이 아니라 근접
        # 방향으로 평균을 왜곡해 오경보가 된다
        if distance_mm < config.TOF_MIN_VALID_CM * 10:
            return config.TOF_OUT_OF_RANGE_CM
        # 상한도 같은 이유로 막는다. 센서 상태가 꼬이면 getDistance()가 65535mm
        # (uint16 최대)를 뱉는데(2026-08-08 실측), 그대로 흘리면 6553.5cm가 되어
        # 로그의 OoR 집계(TOF_OUT_OF_RANGE_CM 기준)에 잡히지 않는다 — 센서가
        # 고장 났는데 "그냥 멀리 있음"으로 보여 analyze_logs.py의 센서 실명
        # 경고를 그대로 통과한다. status 게이트가 꺼진 경우의 안전망이다.
        if distance_mm > config.TOF_OUT_OF_RANGE_CM * 10:
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
