"""VL53L1X 레인징 모드 실측 벤치 (v2.0 로드맵 — SHORT 전환 판단용).

2026-07-28 야외 착용 테스트에서 ToF 값이 직사광에 심하게 오염된 것이 확인됐다
(200ms 간격 50cm 초과 점프 34%, 개활지인데 OoR 판정 0%). SHORT 모드는 주변광
내성이 좋지만 최대 측정 거리가 짧아, 경보 임계값(HIGH 100cm)을 그대로 둘 수
있는지가 불확실하다. 이 스크립트는 그 판단에 필요한 값을 실측한다.

Orange Pi 5에서 직접 실행할 것 (PC에는 VL53L1X 하드웨어가 없다).

측정 항목:
    1. 범위 초과 시 반환값 — 0인지 임의값인지. AlertPolicy의 OoR 해제 백스톱이
       성립하려면 0(→ TOF_OUT_OF_RANGE_CM)이어야 한다.
    2. 유효 최대 거리 — 표적까지의 실제 거리를 --target 으로 알려주고 성공률을 본다.
    3. 노이즈 — 표준편차, 연속 샘플 간 점프 분포.
    4. 초근접(<20cm) 지속 판정 재현 여부.

사용법:
    # 2m 이상 빈 공간을 향하게 두고 (범위 초과 시 반환값 확인)
    python3 scripts/tof_range_bench.py --mode short --seconds 60

    # 표적을 100cm에 두고 (유효 최대 거리 확인)
    python3 scripts/tof_range_bench.py --mode short --seconds 30 --target 100

    # 비교군
    python3 scripts/tof_range_bench.py --mode medium --seconds 60
"""
import argparse
import statistics
import sys
import time
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

_MODES = {"short": 1, "medium": 2, "long": 3}


def collect(mode: int, seconds: float, interval: float) -> List[int]:
    """지정한 레인징 모드로 원시 거리(mm)를 수집한다.

    HAL(VL53L1XHAL)을 거치지 않고 라이브러리를 직접 호출한다 — HAL은 0을
    TOF_OUT_OF_RANGE_CM으로 변환해버려서, 이 벤치의 목적인 "범위 초과 시
    실제 반환값"을 확인할 수 없기 때문이다.

    Args:
        mode: VL53L1X 레인징 모드 (1=SHORT, 2=MEDIUM, 3=LONG).
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

    # aarch64 ctypes 버그 수정 — sensor/vl53l1x_hal.py와 동일
    from ctypes import c_int, c_uint, c_uint16, c_void_p

    lib = VL53L1X._TOF_LIBRARY
    lib.initialise.restype = c_void_p
    lib.startRanging.argtypes = [c_void_p, c_int]
    lib.stopRanging.argtypes = [c_void_p]
    lib.getDistance.argtypes = [c_void_p]
    lib.getDistance.restype = c_uint16
    lib.setMeasurementTimingBudgetMicroSeconds.argtypes = [c_void_p, c_uint]
    lib.setInterMeasurementPeriodMilliSeconds.argtypes = [c_void_p, c_uint]

    tof = VL53L1X.VL53L1X(i2c_bus=config.TOF_I2C_PORT)
    tof.open()
    tof.set_timing(config.TOF_TIMING_BUDGET_US, config.TOF_INTER_MEASUREMENT_MS)
    tof.start_ranging(mode)

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


def report(samples: List[int], mode_name: str, target_cm: Optional[float]) -> None:
    """수집 결과를 KPI 판단에 필요한 형태로 인쇄한다."""
    if not samples:
        print("수집된 샘플이 없습니다.")
        return

    cm = [s / 10.0 for s in samples]
    zeros = sum(1 for s in samples if s == 0)
    jumps = [abs(b - a) for a, b in zip(cm, cm[1:])]
    valid = [d for d in cm if d > 0]

    print(f"\n── {mode_name.upper()} 모드 / {len(samples)}샘플 ─────────────────────────")
    print(f"  0 반환(범위 초과 추정) : {zeros}회 ({zeros / len(samples):.1%})")
    if valid:
        print(f"  유효값 범위 : {min(valid):.1f} ~ {max(valid):.1f}cm")
        print(f"  중앙값 {statistics.median(valid):.1f}cm / 표준편차 "
              f"{statistics.stdev(valid) if len(valid) > 1 else 0.0:.1f}cm")
    if jumps:
        big = sum(1 for j in jumps if j > 50)
        print(f"  연속 샘플 점프 : 중앙값 {statistics.median(jumps):.1f}cm / "
              f"최대 {max(jumps):.1f}cm | 50cm 초과 {big / len(jumps):.1%}")

    near = sum(1 for d in cm if 0 < d < 20)
    print(f"  초근접(<20cm) : {near}회 ({near / len(samples):.1%})")

    print("\n  [판단 근거]")
    # 1. OoR 백스톱 성립 여부
    #    주의: 0 반환이 없다고 해서 "임의값을 뱉는다"고 단정할 수 없다. 시야에 실제
    #    표적이 있으면 정상 측정한 것이다 (2026-07-28 실측: 빈 공간인 줄 알았던 방향에
    #    254cm 벽이 있어 MEDIUM이 표준편차 0.2cm로 정확히 읽었다). 값의 안정성으로 구분한다.
    if zeros:
        print("  · 범위 초과 시 0을 반환한다 → HAL이 TOF_OUT_OF_RANGE_CM으로 매핑하므로")
        print("    AlertPolicy의 OoR 해제 백스톱이 성립한다.")
    elif valid and len(valid) > 1 and statistics.stdev(valid) < 5.0:
        print(f"  · 0 반환이 없고 값이 안정적이다(표준편차 "
              f"{statistics.stdev(valid):.1f}cm) → 시야에 실제 표적이 있어 정상 측정한 것이다.")
        print(f"    범위 초과 거동을 보려면 측정 최대 거리({max(valid):.0f}cm)보다 먼 곳을 향하게 할 것.")
    else:
        print("  · 0 반환이 없는데 값도 불안정하다 → 범위 초과 시 임의값을 반환할 가능성이 있다.")
        print("    사실이면 OoR 백스톱이 무력하므로 HAL 매핑 보강이 필요하다.")

    # 2. 임계값 유지 가능 여부
    if target_cm is not None and valid:
        hit = sum(1 for d in cm if abs(d - target_cm) <= target_cm * 0.2)
        print(f"  · {target_cm:.0f}cm 표적 측정 성공률(±20%): {hit / len(samples):.1%}")
        if target_cm >= config.HIGH_RISK_DIST_CM and hit / len(samples) < 0.8:
            print(f"    ⚠ HIGH 임계값({config.HIGH_RISK_DIST_CM}cm) 거리에서 측정이 불안정하다.")
            print("      SHORT 전환 시 1m 앞 장애물을 놓칠 수 있다 — 전환 중단을 검토할 것.")


def main() -> int:
    """CLI 진입점.

    Returns:
        종료 코드 (0=정상, 1=측정 실패).
    """
    parser = argparse.ArgumentParser(description="VL53L1X 레인징 모드 실측 벤치")
    parser.add_argument("--mode", choices=sorted(_MODES), default="short",
                        help="레인징 모드 (기본: short)")
    parser.add_argument("--seconds", type=float, default=60.0, help="수집 시간 (초)")
    parser.add_argument("--interval", type=float, default=config.TOF_POLL_INTERVAL_SEC,
                        help="폴링 간격 (초)")
    parser.add_argument("--target", type=float, default=None,
                        help="표적까지의 실제 거리 (cm). 주면 측정 성공률을 계산한다")
    args = parser.parse_args()

    print(f"수집 시작: {args.mode} 모드, {args.seconds:.0f}초, {args.interval:.2f}s 간격")
    try:
        samples = collect(_MODES[args.mode], args.seconds, args.interval)
    except RuntimeError as exc:
        print(f"측정 실패: {exc}")
        return 1

    report(samples, args.mode, args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
