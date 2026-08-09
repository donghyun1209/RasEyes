"""VL53L1X RangeStatus 프로브 — "표적 없음"과 "유효 측정"을 구분할 수 있는지 확인한다.

2026-08-08 실외 실측(SHORT, 고정 거치, 5m 트인 공간)에서 0 반환이 0%인데도 값이
49.5~173.7cm로 흩어졌다. 표적이 없는 방향이므로 이 값들은 전부 허수다. HAL은
`0 → TOF_OUT_OF_RANGE_CM` 매핑에 의존하므로, 센서가 0을 주지 않으면 OoR 백스톱이
통째로 무력해진다 (빈 공간에서 중앙값 114.7cm = MID 임계값 150cm 안쪽 상시 발화).

pimoroni 래퍼는 getDistance()만 노출하고 RangeStatus를 버리지만, .so에는 ST 원본
API가 그대로 들어 있고 VL53L1X.py:223이 self._dev를 원본 API에 직접 넘기고 있다 —
즉 핸들이 VL53L1_DEV로 통용된다. 그 경로로 status를 되살린다.

⚠ 구조체 오프셋이 틀리면 RangeStatus가 조용히 쓰레기값이 된다. 그래서 이 스크립트는
RangeMilliMeter가 getDistance()와 일치하는지를 먼저 검증하고, 불일치가 잦으면
status 통계를 신뢰하지 말라고 경고한다.

⚠ I2C가 끊긴 채로 재면 리포트 전체가 허구가 된다 (2026-08-09에 실제로 당했다).
파이썬은 ctypes 콜백 안에서 난 예외를 삼키므로 C 라이브러리는 실패한 읽기를
0으로 채워진 정상 응답으로 처리하고, 거리 0mm·신호 0.02 MCPS가 "측정 결과"로
찍힌다. 그 리포트를 "지면이 안 보인다"로 오독할 뻔했다 — 같은 센서가 20분 뒤
41/41 RANGE_VALID로 멀쩡히 쟀다. 그래서 통신 실패 건수를 세어 판단을 막는다.

Orange Pi 5에서 실행할 것. `raseyes.service`를 반드시 먼저 정지해야 한다 —
켜진 채로 재면 서비스가 같은 I2C 센서를 동시에 잡아 값이 튄다.

사용법:
    # 표적이 없는 방향(5m 이상 트인 곳)을 향하게 두고 — 허수값의 status를 본다
    python3 scripts/tof_status_probe.py --mode short --seconds 30

    # 1m 앞에 벽을 두고 — 유효 측정의 status를 대조군으로 본다
    python3 scripts/tof_status_probe.py --mode short --seconds 30
"""
import argparse
import collections
import statistics
import sys
import time
from ctypes import (POINTER, Structure, byref, c_int, c_int8, c_int16, c_uint,
                    c_uint8, c_uint16, c_uint32, c_void_p, sizeof)
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402

_MODES = {"short": 1, "medium": 2, "long": 3}

# ST VL53L1 API의 RangeStatus 코드 (vl53l1_def.h).
# 2/4/7이 "표적 없음" 계열 — 지금 문제의 후보다.
_STATUS_NAMES = {
    0: "RANGE_VALID (유효)",
    1: "SIGMA_FAIL (노이즈 과다)",
    2: "SIGNAL_FAIL (신호 약함 — 표적 없음/너무 멂)",
    3: "MIN_RANGE_CLIPPED (유효, 최소거리 클립)",
    4: "OUTOFBOUNDS_FAIL (범위 초과)",
    5: "HARDWARE_FAIL",
    6: "NO_WRAP_CHECK (유효, 랩체크 안 함)",
    7: "WRAP_TARGET_FAIL (랩어라운드 — 먼 표적을 가깝게 오독)",
    8: "PROCESSING_FAIL",
    9: "XTALK_SIGNAL_FAIL",
    10: "SYNCRONISATION_INT",
    11: "MERGED_PULSE (유효, 병합 펄스)",
    12: "TARGET_PRESENT_LACK_OF_SIGNAL",
    13: "MIN_RANGE_FAIL",
    14: "RANGE_INVALID",
    255: "NONE (측정 없음)",
}

# RangeStatus가 이 값이면 거리를 신뢰해도 된다는 뜻 (ST 권장: 0만 엄격, 6/11은 관대).
_VALID_STATUSES = {0}

# 구조체 오프셋이 틀렸다고 판정할 불일치 비율.
#
# ⚠ 건수 기준으로 판정하면 안 된다 — 2026-08-09 실측에서 127샘플 중 1건(0.8%)
# 때문에 리포트가 통째로 "판단 불가"로 막혔다. 래퍼 호출과 구조체 읽기 사이에 새
# 측정이 끼어들면 두 거리가 어긋나므로, 값이 튀는 구간에서는 불일치가 정상적으로
# 발생한다 (허용폭을 3σ로 잡아 이미 <1%까지 눌러놨다).
#
# 반대로 오프셋이 진짜 틀렸다면 엉뚱한 멤버를 읽는 것이라 σ와 무관하게 거의 모든
# 샘플이 깨진다(≈100%). 즉 "타이밍 잡음 <1%"와 "오프셋 오류 ≈100%" 사이에는 넓은
# 빈 구간이 있고, 그 한가운데를 자른다.
_BROKEN_RATIO_LIMIT = 0.20

# ctypes 콜백에서 삼켜진 예외(=I2C 통신 실패) 기록. sys.unraisablehook 말고는
# 감지할 경로가 없다 — 파이썬은 "Exception ignored ..."를 stderr에 찍고 넘어간다.
_unraisable: List[str] = []


def _record_unraisable(unraisable) -> None:
    """ctypes 콜백에서 삼켜진 예외를 센다 (첫 1건만 화면에 남긴다).

    2026-08-09 실측: 배선 접촉이 끊긴 채 30초를 재면 이 예외가 227회 발생하는데,
    C 라이브러리는 실패한 읽기를 0으로 채워진 정상 응답으로 처리하므로
    `VL53L1_GetRangingMeasurementData`는 성공(0)을 반환한다. 즉 반환값으로는
    못 잡고 이 훅으로만 잡힌다.

    Args:
        unraisable: sys.unraisablehook이 넘기는 UnraisableHookArgs.
    """
    _unraisable.append(repr(unraisable.exc_value))
    if len(_unraisable) == 1:
        print(f"  ⚠ I2C 통신 실패 감지: {unraisable.exc_value!r}"
              " — 이후 동일 오류는 건수만 셉니다")


class RangingMeasurementData(Structure):
    """ST VL53L1_RangingMeasurementData_t 의 ctypes 대응 구조체.

    필드 순서·크기는 vl53l1_def.h를 따른다. FixPoint1616_t는 uint32다.
    네이티브 정렬 기준 sizeof == 28 이어야 하며, main()이 이를 검사한다.
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


def _patch_ctypes(lib) -> None:
    """aarch64에서 잘리는 핸들 타입을 바로잡는다 (sensor/vl53l1x_hal.py와 동일).

    `initialise.restype = c_void_p`라 핸들이 Python int로 올라오는데, argtypes가
    없으면 32비트 C int로 잘려 segfault가 난다. 이 프로브가 추가로 호출하는
    ST 원본 API 두 개도 같은 이유로 argtypes를 명시한다.

    Args:
        lib: VL53L1X._TOF_LIBRARY (ctypes CDLL 객체).
    """
    lib.initialise.restype = c_void_p
    lib.startRanging.argtypes = [c_void_p, c_int]
    lib.stopRanging.argtypes = [c_void_p]
    lib.getDistance.argtypes = [c_void_p]
    lib.getDistance.restype = c_uint16
    lib.setMeasurementTimingBudgetMicroSeconds.argtypes = [c_void_p, c_uint]
    lib.setInterMeasurementPeriodMilliSeconds.argtypes = [c_void_p, c_uint]

    # 이 프로브의 핵심 — 래퍼가 노출하지 않는 ST 원본 API
    lib.VL53L1_GetRangingMeasurementData.argtypes = [
        c_void_p, POINTER(RangingMeasurementData)
    ]
    lib.VL53L1_GetRangingMeasurementData.restype = c_int8  # VL53L1_Error


def collect(mode: int, seconds: float,
            interval: float) -> Tuple[List[Tuple[int, int, int, int]], int]:
    """거리(mm)와 RangeStatus를 함께 수집한다.

    pimoroni의 get_distance()를 먼저 호출해 측정 흐름(wait → get → clear)을 그대로
    타고, 곧바로 GetRangingMeasurementData로 같은 측정의 부가 정보를 다시 읽는다.
    측정 시퀀스를 직접 돌리지 않으므로 래퍼와 충돌할 여지가 없다.

    수집하는 동안 `sys.unraisablehook`을 걸어 I2C 통신 실패를 센다. 이 예외는
    ctypes 콜백 안에서 나 파이썬이 삼키고, C 라이브러리는 실패한 읽기를 정상
    응답으로 처리하므로 반환값·거리값 어느 쪽으로도 감지되지 않는다.

    Args:
        mode: VL53L1X 레인징 모드 (1=SHORT, 2=MEDIUM, 3=LONG).
        seconds: 수집 시간 (초).
        interval: 폴링 간격 (초).

    Returns:
        (샘플 목록, I2C 통신 실패 건수). 샘플은
        (getDistance mm, 구조체 RangeMilliMeter, RangeStatus, SignalRate raw) 튜플.

    Raises:
        RuntimeError: VL53L1X 패키지 미설치 또는 센서 초기화 실패 시.
    """
    try:
        import VL53L1X  # noqa: N813
    except ImportError as exc:
        raise RuntimeError("VL53L1X 패키지가 필요합니다: pip3 install VL53L1X") from exc

    lib = VL53L1X._TOF_LIBRARY
    _patch_ctypes(lib)

    tof = VL53L1X.VL53L1X(i2c_bus=config.TOF_I2C_PORT)
    tof.open()
    tof.set_timing(config.TOF_TIMING_BUDGET_US, config.TOF_INTER_MEASUREMENT_MS)
    tof.start_ranging(mode)

    data = RangingMeasurementData()
    samples: List[Tuple[int, int, int, int]] = []
    deadline = time.monotonic() + seconds
    _unraisable.clear()
    prev_hook = sys.unraisablehook
    sys.unraisablehook = _record_unraisable
    try:
        while time.monotonic() < deadline:
            wrapper_mm = tof.get_distance()
            err = lib.VL53L1_GetRangingMeasurementData(tof._dev, byref(data))
            if err != 0:
                print(f"  ⚠ GetRangingMeasurementData 오류 {err} — 이 샘플 건너뜀")
            else:
                samples.append(
                    (wrapper_mm, data.RangeMilliMeter, data.RangeStatus,
                     data.SignalRateRtnMegaCps)
                )
            time.sleep(interval)
    finally:
        sys.unraisablehook = prev_hook
        tof.stop_ranging()
        tof.close()
    return samples, len(_unraisable)


def report(samples: List[Tuple[int, int, int, int]], mode_name: str,
           i2c_errors: int = 0) -> None:
    """status별 분포를 인쇄하고, 숫자를 믿어도 되는지 먼저 두 겹으로 검증한다.

    검증 순서가 중요하다 — I2C가 끊겼으면 구조체 매핑이 맞든 틀리든 숫자 자체가
    측정이 아니므로, 통신을 먼저 본다.

    Args:
        samples: collect()가 반환한 튜플 목록.
        mode_name: 레인징 모드 이름 (출력용).
        i2c_errors: 수집 중 감지한 I2C 통신 실패 건수.
    """
    if not samples:
        print("수집된 샘플이 없습니다.")
        return

    print(f"\n── {mode_name.upper()} 모드 / {len(samples)}샘플 ─────────────────────────")

    # ── 0-a. I2C 통신 검증 ───────────────────────────────────────
    # 이게 깨지면 아래 숫자는 측정이 아니라 0으로 채워진 실패한 읽기다.
    print(f"  [I2C 통신] 실패 {i2c_errors}건")
    if i2c_errors:
        print("  ⚠ 통신이 끊긴 채로 측정됐다 → 아래 거리·status·신호는 전부 허구일 수 있다.")
        print("     배선 커넥터를 다시 꽂고(양끝) 센서를 고정한 뒤 재측정할 것.")

    # ── 0-b. 구조체 매핑 검증 ────────────────────────────────────
    # RangeMilliMeter가 래퍼 값과 어긋나면 그 뒤에 있는 RangeStatus도 못 믿는다.
    #
    # ⚠ 단순 "값이 다르면 실패"로 판정하면 안 된다. get_distance()와 이 구조체 읽기는
    # 서로 다른 측정을 볼 수 있어서, 값이 튀는 구간일수록 불일치가 늘어난다 —
    # 2026-08-08 실측에서 불일치 건수가 거리 표준편차와 정확히 비례했다
    # (실내 σ0.1cm→0건 / 야외 σ24cm→7% / 지면 σ93cm→91%). 이걸 오프셋 오류로
    # 오판해서 멀쩡한 야외 데이터를 하마터면 버릴 뻔했다.
    #
    # 오프셋이 틀리면 값이 안정적인 구간에서도 어긋나므로, 판정 기준을 거리 자체의
    # 변동폭(3σ)에 맞춘다. 변동폭 안의 불일치는 측정 타이밍 차이다.
    # 그러고도 남는 소수 불일치는 _BROKEN_RATIO_LIMIT으로 흡수한다.
    dists = [s for _, s, _, _ in samples]
    sigma = statistics.stdev(dists) if len(dists) > 1 else 0.0
    tolerance = max(3 * sigma, 100.0)  # 안정적인 구간에서도 100mm는 허용
    diffs = [abs(w - s) for w, s, _, _ in samples]
    broken = [(w, s) for (w, s, _, _), d in zip(samples, diffs) if d > tolerance]
    broken_ratio = len(broken) / len(samples)
    struct_ok = broken_ratio <= _BROKEN_RATIO_LIMIT
    print(f"  [구조체 검증] sizeof={sizeof(RangingMeasurementData)}바이트 (기대 28)")
    if broken:
        print(f"  · 허용폭({tolerance:.0f}mm = max(3σ, 100))을 넘은 샘플 {len(broken)}건 "
              f"({broken_ratio:.1%}) — 예: 래퍼 {broken[0][0]}mm vs 구조체 {broken[0][1]}mm")
    if struct_ok:
        print(f"  ✅ 구조체 매핑 정상 (불일치 {broken_ratio:.1%} ≤ 한계 "
              f"{_BROKEN_RATIO_LIMIT:.0%}) → RangeStatus 신뢰 가능")
    else:
        print(f"  ⚠ 불일치 {broken_ratio:.1%} > 한계 {_BROKEN_RATIO_LIMIT:.0%} → "
              "구조체 오프셋이 틀렸다. 아래 status 통계를 신뢰하지 말 것.")

    # ── 1. status 분포 ──────────────────────────────────────────
    counts = collections.Counter(st for _, _, st, _ in samples)
    print("\n  [RangeStatus 분포]")
    for status, n in counts.most_common():
        name = _STATUS_NAMES.get(status, f"UNKNOWN({status})")
        print(f"    {status:>3} {name:<44} {n:>4}회 ({n / len(samples):5.1%})")

    # ── 2. 유효/무효별 거리 분포 ─────────────────────────────────
    # 핵심 질문: 빈 공간에서 나온 그 허수값들이 status로 걸러지는가?
    for label, keep in (("유효(status∈{0})", True), ("무효(그 외)", False)):
        group = [s / 10.0 for _, s, st, _ in samples
                 if (st in _VALID_STATUSES) == keep]
        if not group:
            print(f"\n  [{label}] 0건")
            continue
        med = statistics.median(group)
        sd = statistics.stdev(group) if len(group) > 1 else 0.0
        print(f"\n  [{label}] {len(group)}건 ({len(group) / len(samples):.1%})")
        print(f"    범위 {min(group):.1f}~{max(group):.1f}cm / "
              f"중앙값 {med:.1f}cm / 표준편차 {sd:.1f}cm")

    # ── 3. 신호 강도 ────────────────────────────────────────────
    # FixPoint1616 → MCPS. 표적이 없으면 낮게 나온다.
    rates = [r / 65536.0 for _, _, _, r in samples]
    print(f"\n  [신호 강도] 중앙값 {statistics.median(rates):.2f} MCPS "
          f"/ 범위 {min(rates):.2f}~{max(rates):.2f}")

    # ── 4. 판단 ─────────────────────────────────────────────────
    valid_ratio = sum(counts[s] for s in _VALID_STATUSES) / len(samples)
    print("\n  [판단 근거]")
    if i2c_errors:
        print(f"  · I2C 통신 실패 {i2c_errors}건 — 판단 불가. 배선부터 잡을 것.")
        print("    이 상태의 숫자를 '표적 없음'으로 읽으면 센서가 멀쩡한데도")
        print("    '아무것도 안 보인다'는 결론이 나온다 (2026-08-09에 겪음).")
    elif not struct_ok:
        print("  · 구조체 검증 실패 — 판단 불가. 오프셋을 먼저 맞출 것.")
    elif valid_ratio < 0.5:
        print(f"  · 유효 판정이 {valid_ratio:.1%}뿐이다 → status로 허수값을 걸러낼 수 있다.")
        print("    HAL이 RangeStatus를 읽어 무효 샘플을 TOF_OUT_OF_RANGE_CM으로")
        print("    매핑하면 OoR 백스톱이 되살아난다.")
    else:
        print(f"  · 유효 판정이 {valid_ratio:.1%}다 → 센서는 이 값들을 유효하다고 본다.")
        print("    표적 없는 방향에서 이 결과라면 status로도 못 거른다는 뜻이므로,")
        print("    거리 자체가 아니라 신호 강도(MCPS) 임계값을 검토해야 한다.")


def main() -> int:
    """CLI 진입점.

    Returns:
        종료 코드 (0=정상, 1=측정 실패).
    """
    parser = argparse.ArgumentParser(description="VL53L1X RangeStatus 프로브")
    parser.add_argument("--mode", choices=sorted(_MODES), default="short",
                        help="레인징 모드 (기본: short)")
    parser.add_argument("--seconds", type=float, default=30.0, help="수집 시간 (초)")
    parser.add_argument("--interval", type=float, default=config.TOF_POLL_INTERVAL_SEC,
                        help="폴링 간격 (초)")
    args = parser.parse_args()

    print(f"수집 시작: {args.mode} 모드, {args.seconds:.0f}초, {args.interval:.2f}s 간격")
    try:
        samples, i2c_errors = collect(_MODES[args.mode], args.seconds, args.interval)
    except RuntimeError as exc:
        print(f"측정 실패: {exc}")
        return 1

    report(samples, args.mode, i2c_errors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
