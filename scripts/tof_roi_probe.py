"""VL53L1X 시야(ROI) 방향 실측 프로브 (v2.0 로드맵 5-3/5-4 — 지면 반사 배제용).

2026-08-04 야외 86분 로그에서 ToF 유효 측정 2216개 중 2031개(92%)가 100~124cm
한 구간에 몰렸고, 150cm 초과는 전체 샘플의 1.6%뿐이었다. FoV 27° 콘이라 가슴
높이에서 조금만 아래로 기울어도 지면이 걸리는데, 이 분포가 정확히 그 모양이다.
SPAD 16x16 격자 중 절반만 쓰면 물리 가림막 없이 그 방향을 잘라낼 수 있다.

**이 스크립트가 필요한 이유:** 격자의 Y축이 실제 장면의 위/아래 중 어디에
대응하는지는 센서 광학 방향과 모듈 장착 방향에 달려 있어 코드로 판별할 수 없다
(카메라부터가 상하 반전 장착이라 CSI_ROTATE_180=True). 반대쪽을 자르면 머리 높이
장애물을 못 보게 되어 제품 전제가 무너지므로, 켜기 전에 반드시 실측한다.

Orange Pi 5에서 직접 실행할 것 (PC에는 VL53L1X 하드웨어가 없다).

전제:
    1. 서비스를 먼저 정지한다 — I2C 버스를 두 프로세스가 동시에 쓸 수 없다.
           sudo systemctl stop raseyes.service
    2. **실제 착용 각도로 세워둔 상태**에서 실행한다. 책상에 눕혀 놓고 재면
       지면이 어느 절반에 걸리는지 알 수 없어 측정 자체가 무의미하다.
    3. 측정 중에는 기기를 움직이지 않는다 (ROI별 값을 비교하는 것이 목적).

사용법:
    sudo systemctl stop raseyes.service
    python3 scripts/tof_roi_probe.py
    python3 scripts/tof_roi_probe.py --seconds 20    # ROI당 수집 시간 조절

판정:
    상반부/하반부 중 **더 짧고 안정적인 거리를 보고하는 쪽이 지면**을 보고 있다.
    그 **반대쪽** 좌표를 config.py의 TOF_ROI_* 에 넣고 TOF_ROI_ENABLED=True 로 켠다.
"""
import argparse
import statistics
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

# (이름, top_left_x, top_left_y, bot_right_x, bot_right_y)
# 좌표는 0~15, 최소 ROI는 4x4다.
_ROI_CANDIDATES: List[Tuple[str, int, int, int, int]] = [
    ("전체 (16x16)", 0, 15, 15, 0),
    ("상반부 (16x8)", 0, 15, 15, 8),
    ("하반부 (16x8)", 0, 7, 15, 0),
    ("중앙 (8x8)", 4, 11, 11, 4),
]


def collect(
    roi: Tuple[int, int, int, int], seconds: float, interval: float
) -> List[int]:
    """지정한 ROI로 원시 거리(mm)를 수집한다.

    HAL(VL53L1XHAL)을 거치지 않고 라이브러리를 직접 호출한다 — HAL은 0과 초근접
    쓰레기값을 TOF_OUT_OF_RANGE_CM으로 변환해버려서, ROI별 실패 양상(0 반환 비율)을
    구별할 수 없기 때문이다. scripts/tof_range_bench.py와 같은 방식이다.

    Args:
        roi: (top_left_x, top_left_y, bot_right_x, bot_right_y).
        seconds: 수집 시간 (초).
        interval: 폴링 간격 (초).

    Returns:
        원시 거리 측정값(mm) 목록.

    Raises:
        RuntimeError: VL53L1X 패키지 미설치 또는 센서 초기화 실패 시.
    """
    try:
        import VL53L1X  # noqa: N813
    except ImportError as exc:
        raise RuntimeError("VL53L1X 패키지가 필요합니다: pip3 install VL53L1X") from exc

    # aarch64 ctypes 버그 수정 — sensor/vl53l1x_hal.py와 동일.
    # setUserRoi의 argtypes가 빠지면 64비트 핸들이 32비트로 잘려 segfault 난다.
    from ctypes import c_int, c_uint, c_uint8, c_uint16, c_void_p

    lib = VL53L1X._TOF_LIBRARY
    lib.initialise.restype = c_void_p
    lib.startRanging.argtypes = [c_void_p, c_int]
    lib.stopRanging.argtypes = [c_void_p]
    lib.getDistance.argtypes = [c_void_p]
    lib.getDistance.restype = c_uint16
    lib.setMeasurementTimingBudgetMicroSeconds.argtypes = [c_void_p, c_uint]
    lib.setInterMeasurementPeriodMilliSeconds.argtypes = [c_void_p, c_uint]
    lib.setUserRoi.argtypes = [c_void_p, c_uint8, c_uint8, c_uint8, c_uint8]

    tof = VL53L1X.VL53L1X(i2c_bus=config.TOF_I2C_PORT)
    tof.open()
    tof.set_timing(config.TOF_TIMING_BUDGET_US, config.TOF_INTER_MEASUREMENT_MS)
    # 클래스명은 소문자 x다 (VL53L1XUserRoi는 존재하지 않는다)
    tof.set_user_roi(VL53L1X.VL53L1xUserRoi(*roi))
    tof.start_ranging(config.TOF_RANGING_MODE_MEDIUM)

    samples: List[int] = []
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            samples.append(tof.get_distance())
            time.sleep(interval)
    finally:
        tof.stop_ranging()
        tof.close()
    return samples


def report(name: str, samples: List[int]) -> Optional[float]:
    """수집 결과를 인쇄하고 유효 측정의 중앙값(cm)을 반환한다.

    Args:
        name: ROI 이름 (출력용).
        samples: 원시 거리 측정값(mm) 목록.

    Returns:
        유효 측정의 중앙값(cm). 유효 측정이 없으면 None.
    """
    if not samples:
        print(f"  {name:16s} : 샘플 없음")
        return None

    oor_mm = config.TOF_OUT_OF_RANGE_CM * 10
    min_mm = config.TOF_MIN_VALID_CM * 10
    valid = [s for s in samples if min_mm <= s < oor_mm]
    invalid_ratio = (len(samples) - len(valid)) / len(samples) * 100

    if not valid:
        print(
            f"  {name:16s} : n={len(samples):4d}  유효 0개 "
            f"(무효/OoR {invalid_ratio:.1f}%)"
        )
        return None

    valid_cm = sorted(v / 10.0 for v in valid)
    median = statistics.median(valid_cm)
    p10 = valid_cm[int(len(valid_cm) * 0.10)]
    p90 = valid_cm[int(len(valid_cm) * 0.90)]
    stdev = statistics.stdev(valid_cm) if len(valid_cm) > 1 else 0.0

    print(
        f"  {name:16s} : n={len(samples):4d}  중앙값 {median:6.1f}cm  "
        f"p10 {p10:6.1f}  p90 {p90:6.1f}  σ {stdev:5.1f}  "
        f"무효/OoR {invalid_ratio:5.1f}%"
    )
    return median


def main() -> int:
    """ROI 후보들을 순차 적용하며 측정하고 방향 판정 힌트를 인쇄한다.

    Returns:
        종료 코드 (0=정상, 1=오류).
    """
    parser = argparse.ArgumentParser(description="VL53L1X ROI 방향 실측 프로브")
    parser.add_argument(
        "--seconds", type=float, default=15.0, help="ROI당 수집 시간 (초)"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=config.TOF_POLL_INTERVAL_SEC,
        help="폴링 간격 (초)",
    )
    args = parser.parse_args()

    print("VL53L1X ROI 프로브")
    print(f"  ROI당 {args.seconds:.0f}초 x {len(_ROI_CANDIDATES)}개 "
          f"= 약 {args.seconds * len(_ROI_CANDIDATES) / 60:.1f}분 소요")
    print("  ⚠️ 실제 착용 각도로 세워둔 채, 측정 중 움직이지 마세요.\n")

    medians = {}
    for name, tlx, tly, brx, bry in _ROI_CANDIDATES:
        print(f"[{name}] 수집 중...", flush=True)
        try:
            samples = collect((tlx, tly, brx, bry), args.seconds, args.interval)
        except RuntimeError as exc:
            print(f"오류: {exc}", file=sys.stderr)
            return 1
        medians[name] = report(name, samples)

    print("\n── 판정 ─────────────────────────────────")
    top = medians.get("상반부 (16x8)")
    bottom = medians.get("하반부 (16x8)")

    if top is None and bottom is None:
        print("  두 절반 모두 유효 측정이 없습니다. 표적을 두고 다시 측정하세요.")
    elif top is None or bottom is None:
        seen, blind = ("상반부", "하반부") if bottom is None else ("하반부", "상반부")
        print(f"  {blind}는 아무것도 못 봤고 {seen}만 측정됐습니다.")
        print(f"  → {seen} 쪽에 장애물/지면이 있습니다. 어느 쪽인지 눈으로 확인하세요.")
    else:
        near, far = ("상반부", "하반부") if top < bottom else ("하반부", "상반부")
        print(f"  상반부 {top:.1f}cm vs 하반부 {bottom:.1f}cm "
              f"(차이 {abs(top - bottom):.1f}cm)")
        if abs(top - bottom) < 10.0:
            print("  두 절반의 차이가 10cm 미만입니다 — 방향을 가릴 만한 차이가")
            print("  아닙니다. 지면이 확실히 한쪽에만 걸리도록 각도를 조정해 다시 재세요.")
        else:
            print(f"  → {near}가 더 가까운 것을 보고 있습니다. 그게 지면이라면,")
            print(f"     **{far}만 남기도록** config.py의 TOF_ROI_* 를 설정하세요.")

    print("\n  설정 예 (상반부만 사용):")
    print("    TOF_ROI_ENABLED = True")
    print("    TOF_ROI_TOP_LEFT_X, TOF_ROI_TOP_LEFT_Y = 0, 15")
    print("    TOF_ROI_BOT_RIGHT_X, TOF_ROI_BOT_RIGHT_Y = 15, 8")
    print("  설정 예 (하반부만 사용):")
    print("    TOF_ROI_TOP_LEFT_X, TOF_ROI_TOP_LEFT_Y = 0, 7")
    print("    TOF_ROI_BOT_RIGHT_X, TOF_ROI_BOT_RIGHT_Y = 15, 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
