"""RasEyes 운영 CSV 로그 분석기 (v2.0 로드맵 2-2).

착용 테스트로 수집한 CSV에서 FPS 방어율·CPU 온도·알람 빈도·E2E 레이턴시를
세션별로 산출해 KPI(15+ FPS, E2E < 500ms, 오탐지 < 1회/분)와 대조한다.

알려진 데이터 함정 2건을 반영한다:
    1. 타임존 경계 — Pi 타임존이 Asia/Shanghai(+0800)였던 구간의 CSV는 실제
       한국 시각보다 1시간 이르다. --tz-fix-before 로 해당 행에 +1h 보정한다.
    2. 세션 첫 행 이상치 — fps가 EMA 초기화 아티팩트로 비정상값(실측 예: 1164)을
       가지므로 FPS 통계에서 각 세션의 첫 행을 제외한다.

사용법:
    python scripts/analyze_logs.py logs_archive/*.csv
    python scripts/analyze_logs.py logs_archive/*.csv --tz-fix-before 2026-07-27T00:00:00
"""
import argparse
import csv
import datetime
import glob
import statistics
import sys
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, ".")

import config  # noqa: E402  (프로젝트 루트 기준 실행 전제)

#: 이 간격 이상 timestamp가 벌어지면 별개 세션으로 간주한다.
SESSION_GAP_SEC = 60.0


class Session:
    """연속된 로그 행 묶음 하나 (프로세스 1회 실행에 대응).

    Args:
        source: 원본 CSV 파일 경로.
        index: 파일 내 세션 순번 (0부터).
    """

    def __init__(self, source: str, index: int) -> None:
        self.source = source
        self.index = index
        self.rows: List[Dict[str, str]] = []
        self.times: List[datetime.datetime] = []

    @property
    def label(self) -> str:
        """사람이 읽을 세션 식별자."""
        return "{}#{}".format(self.source.rsplit("/", 1)[-1], self.index)

    @property
    def duration_sec(self) -> float:
        """세션 지속 시간 (초). 행이 1개 이하이면 0.0."""
        if len(self.times) < 2:
            return 0.0
        return (self.times[-1] - self.times[0]).total_seconds()


def _parse_float(value: str, default: float = 0.0) -> float:
    """CSV 셀을 float으로 변환한다. 빈 값/파싱 실패 시 default를 반환한다."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_time(value: str) -> Optional[datetime.datetime]:
    """ISO8601 timestamp를 파싱한다. 실패하면 None."""
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def load_sessions(paths: Sequence[str], tz_fix_before: Optional[datetime.datetime]) -> List[Session]:
    """CSV 파일들을 읽어 세션 단위로 분할한다.

    CsvLogger가 mode="w"로 파일을 열기 때문에 원칙적으로 파일 1개 = 세션 1개지만,
    append로 운용된 과거 로그를 대비해 timestamp 역행 또는 SESSION_GAP_SEC 초과
    간격도 세션 경계로 취급한다.

    Args:
        paths: 읽을 CSV 파일 경로 목록.
        tz_fix_before: 이 시각 이전 행에 +1시간을 더한다. None이면 보정하지 않는다.

    Returns:
        행이 1개 이상인 Session 목록.
    """
    sessions: List[Session] = []
    for path in paths:
        try:
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except OSError as exc:
            print("! 읽기 실패: {} ({})".format(path, exc))
            continue

        current = Session(path, 0)
        prev_time: Optional[datetime.datetime] = None
        for row in rows:
            stamp = _parse_time(row.get("timestamp", ""))
            if stamp is None:
                continue
            if tz_fix_before is not None and stamp < tz_fix_before:
                stamp += datetime.timedelta(hours=1)

            if prev_time is not None:
                gap = (stamp - prev_time).total_seconds()
                if gap < 0 or gap > SESSION_GAP_SEC:
                    if current.rows:
                        sessions.append(current)
                    current = Session(path, current.index + 1)
            current.rows.append(row)
            current.times.append(stamp)
            prev_time = stamp

        if current.rows:
            sessions.append(current)
    return sessions


def _pct(values: List[float], q: float) -> float:
    """정렬 기반 백분위수. 값이 없으면 0.0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return ordered[idx]


def summarize(session: Session) -> Dict[str, float]:
    """세션 하나의 KPI 지표를 계산한다.

    Args:
        session: 분석할 세션.

    Returns:
        지표 이름 → 값 딕셔너리.
    """
    rows = session.rows
    # 세션 첫 행 fps는 EMA 초기화 아티팩트라 FPS 통계에서만 제외한다.
    fps_values = [_parse_float(r.get("fps", "")) for r in rows[1:]]
    temps = [t for t in (_parse_float(r.get("cpu_temp", "")) for r in rows) if t > 0.0]
    latencies = [l for l in (_parse_float(r.get("latency_ms", "")) for r in rows) if l > 0.0]
    alerts = sum(1 for r in rows if str(r.get("alert_triggered", "")).lower() == "true")
    occlusions = sum(int(_parse_float(r.get("occlusion_alerts", ""))) for r in rows)

    # 아래 3개는 경보 정책 도입(2026-07-28) 이후 세션에만 존재한다.
    # 이전 아카이브는 .get()이 ""를 반환해 0으로 집계된다.
    emitted = sum(int(_parse_float(r.get("alerts_emitted", ""))) for r in rows)
    tof_raws = [_parse_float(r.get("tof_raw_cm", "")) for r in rows if r.get("tof_raw_cm")]
    tof_only = [_parse_float(r.get("tof_only_ratio", "")) for r in rows if r.get("tof_only_ratio")]

    minutes = session.duration_sec / 60.0
    return {
        "rows": float(len(rows)),
        "duration_min": minutes,
        "fps_mean": statistics.fmean(fps_values) if fps_values else 0.0,
        "fps_median": statistics.median(fps_values) if fps_values else 0.0,
        "fps_kpi_ratio": (
            sum(1 for v in fps_values if v >= config.TARGET_FPS) / len(fps_values)
            if fps_values else 0.0
        ),
        "fps_fallback_ratio": (
            sum(1 for v in fps_values if v < config.FPS_FALLBACK_THRESHOLD) / len(fps_values)
            if fps_values else 0.0
        ),
        "temp_mean": statistics.fmean(temps) if temps else 0.0,
        "temp_max": max(temps) if temps else 0.0,
        "temp_throttle_ratio": (
            sum(1 for t in temps if t > config.THERMAL_THROTTLE_TEMP_C) / len(temps)
            if temps else 0.0
        ),
        "latency_mean": statistics.fmean(latencies) if latencies else 0.0,
        "latency_p95": _pct(latencies, 0.95),
        "latency_over_ratio": (
            sum(1 for l in latencies if l > config.LATENCY_WARN_THRESHOLD_MS) / len(latencies)
            if latencies else 0.0
        ),
        "alerts": float(alerts),
        "alerts_per_min": alerts / minutes if minutes > 0 else 0.0,
        "occlusions": float(occlusions),
        "emitted": float(emitted),
        "emitted_per_min": emitted / minutes if minutes > 0 else 0.0,
        "tof_oor_ratio": (
            sum(1 for d in tof_raws if d >= config.TOF_OUT_OF_RANGE_CM) / len(tof_raws)
            if tof_raws else 0.0
        ),
        "tof_only_ratio": statistics.fmean(tof_only) if tof_only else 0.0,
        "has_diagnostics": 1.0 if tof_raws else 0.0,
    }


def print_report(session: Session, stats: Dict[str, float]) -> None:
    """세션 하나의 분석 결과를 표준 출력에 인쇄한다."""
    print("\n── {} ─────────────────────────".format(session.label))
    if session.times:
        print("  기간      : {} ~ {} ({:.1f}분, {:.0f}행)".format(
            session.times[0].isoformat(timespec="seconds"),
            session.times[-1].isoformat(timespec="seconds"),
            stats["duration_min"], stats["rows"]))
    print("  FPS       : 평균 {:.1f} / 중앙 {:.1f} | {}FPS 방어율 {:.1%} | fallback(<{}) {:.1%}".format(
        stats["fps_mean"], stats["fps_median"], config.TARGET_FPS,
        stats["fps_kpi_ratio"], config.FPS_FALLBACK_THRESHOLD, stats["fps_fallback_ratio"]))
    print("  CPU 온도  : 평균 {:.1f}°C / 최대 {:.1f}°C | 스로틀({}°C 초과) {:.1%}".format(
        stats["temp_mean"], stats["temp_max"],
        config.THERMAL_THROTTLE_TEMP_C, stats["temp_throttle_ratio"]))
    print("  레이턴시  : 평균 {:.1f}ms / p95 {:.1f}ms | {}ms 초과 {:.1%}".format(
        stats["latency_mean"], stats["latency_p95"],
        config.LATENCY_WARN_THRESHOLD_MS, stats["latency_over_ratio"]))
    print("  위험 상태 : {:.0f}초 | 분당 {:.2f}초 (거리 임계값 이내였던 시간)".format(
        stats["alerts"], stats["alerts_per_min"]))
    if stats["has_diagnostics"]:
        print("  경보 발화 : {:.0f}회 | 분당 {:.2f}회 (KPI: < 1회/분) {}".format(
            stats["emitted"], stats["emitted_per_min"],
            "✓" if stats["emitted_per_min"] < 1.0 else "✗"))
        # 경보가 줄어든 것이 개선인지 센서 실명인지 구별하기 위한 지표.
        # OoR이 지나치게 높으면 아무것도 못 보는 상태로 조용해진 것이다.
        oor_flag = " ⚠ 센서 실명 의심" if stats["tof_oor_ratio"] > 0.7 else ""
        print("  ToF OoR   : {:.1%}{}".format(stats["tof_oor_ratio"], oor_flag))
        print("  ToF 단독  : {:.1%} (비전 실명 정도 — 높을수록 카메라가 못 보고 있음)".format(
            stats["tof_only_ratio"]))
    else:
        print("  경보 발화 : (진단 컬럼 없음 — 경보 정책 도입 이전 세션)")
    print("  가림 경고 : {:.0f}회".format(stats["occlusions"]))


def main() -> int:
    """CLI 진입점.

    Returns:
        종료 코드 (0=정상, 1=분석할 데이터 없음).
    """
    parser = argparse.ArgumentParser(description="RasEyes 운영 CSV 로그 분석")
    parser.add_argument("paths", nargs="+", help="분석할 CSV 파일 경로 (glob 가능)")
    parser.add_argument(
        "--tz-fix-before",
        default=None,
        help="이 ISO8601 시각 이전 행에 +1h 보정 (Pi가 Asia/Shanghai였던 구간). 예: 2026-07-27T00:00:00",
    )
    args = parser.parse_args()

    tz_fix_before = None
    if args.tz_fix_before:
        tz_fix_before = _parse_time(args.tz_fix_before)
        if tz_fix_before is None:
            parser.error("--tz-fix-before 파싱 실패: {}".format(args.tz_fix_before))

    expanded: List[str] = []
    for pattern in args.paths:
        expanded.extend(sorted(glob.glob(pattern)) or [pattern])

    sessions = load_sessions(expanded, tz_fix_before)
    if not sessions:
        print("분석할 행이 없습니다. (파일 {}개 확인)".format(len(expanded)))
        return 1

    print("세션 {}개 / 파일 {}개 분석".format(len(sessions), len(expanded)))
    if tz_fix_before is not None:
        print("타임존 보정: {} 이전 행에 +1h 적용".format(tz_fix_before.isoformat()))

    total_rows = 0
    total_min = 0.0
    total_alerts = 0.0
    total_emitted = 0.0
    diag_min = 0.0
    for session in sessions:
        stats = summarize(session)
        print_report(session, stats)
        total_rows += len(session.rows)
        total_min += stats["duration_min"]
        total_alerts += stats["alerts"]
        if stats["has_diagnostics"]:
            total_emitted += stats["emitted"]
            diag_min += stats["duration_min"]

    print("\n── 전체 ─────────────────────────")
    print("  총 {}행 / {:.1f}분 / 위험 상태 {:.0f}초 (분당 {:.2f}초)".format(
        total_rows, total_min, total_alerts,
        total_alerts / total_min if total_min > 0 else 0.0))
    if diag_min > 0:
        print("  경보 발화 {:.0f}회 / {:.1f}분 = 분당 {:.2f}회 (KPI: < 1회/분) {}".format(
            total_emitted, diag_min, total_emitted / diag_min,
            "✓" if total_emitted / diag_min < 1.0 else "✗"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
