"""RasEyes 운영 로그 뷰어 — 로컬 HTTP 서버 (읽기 전용).

수집한 세션 CSV와 이벤트 클립을 브라우저에서 함께 훑어보기 위한 뷰어다.
`analyze_logs.py`의 세션 분할·KPI 산출 로직을 그대로 재사용하므로, 터미널
리포트와 화면의 숫자가 어긋나지 않는다.

표준 라이브러리만 사용한다 (추가 의존성 없음). 서버는 파일을 읽기만 하며
쓰기·삭제 경로가 없다.

사용법:
    python scripts/log_viewer.py                       # http://0.0.0.0:8765
    python scripts/log_viewer.py --root logs           # Pi 운영 로그 폴더
    python scripts/log_viewer.py --host 127.0.0.1      # 이 머신에서만 접속
    python scripts/log_viewer.py --tz-fix-before 2026-07-27T00:00:00

Tailscale 너머의 다른 기기에서 볼 때는 기본 바인드(0.0.0.0) 그대로 두고
이 머신의 tailscale IP로 접속한다 (예: http://100.73.229.60:8765).
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

import analyze_logs  # noqa: E402  (세션 분할·KPI 산출 재사용)
import config  # noqa: E402

#: 프런트엔드 단일 파일. 요청마다 다시 읽으므로 편집이 새로고침만으로 반영된다.
_HTML_PATH = Path(__file__).resolve().with_name("log_viewer.html")

#: 클립 디렉터리명 앞부분: <YYYYmmdd>_<HHMMSS>_<label>_<dist>cm
_CLIP_NAME_RE = re.compile(r"^(\d{8})_(\d{6})_")

#: 그래프·툴팁에 실어 보내는 수치 컬럼.
_NUMERIC_COLUMNS = (
    "fps", "tof_raw_cm", "tof_distance_cm", "latency_ms", "frame_luma",
    "exposure", "gain", "cpu_temp", "tof_only_ratio", "no_detect_ratio",
)

#: 이 로그 주기의 '증가분' 카운터 컬럼.
_COUNT_COLUMNS = ("alerts_emitted", "occlusion_alerts", "mid_suppressed")


def _parse_time(value: str) -> Optional[datetime.datetime]:
    """ISO8601 문자열을 파싱한다. 실패하면 None."""
    return analyze_logs._parse_time(value)


def _shift(stamp: datetime.datetime,
           tz_fix_before: Optional[datetime.datetime]) -> datetime.datetime:
    """타임존 보정 구간이면 +1시간을 더한다.

    Pi 타임존이 Asia/Shanghai(+0800)였던 구간의 기록은 실제 한국 시각보다
    1시간 이르다. CSV와 클립에 같은 규칙을 적용해야 캘린더에서 두 데이터가
    같은 날짜에 놓인다.

    Args:
        stamp: 원본 시각.
        tz_fix_before: 이 시각 이전이면 보정한다. None이면 보정하지 않는다.

    Returns:
        보정된 시각.
    """
    if tz_fix_before is not None and stamp < tz_fix_before:
        return stamp + datetime.timedelta(hours=1)
    return stamp


def _cell(row: Dict[str, str], key: str) -> Optional[float]:
    """CSV 셀을 float으로 읽는다. 빈 값이면 None (0.0으로 뭉개지 않는다).

    결측을 0으로 집계하면 진단 컬럼이 없던 과거 세션이 "전부 암흑/정지"로
    오독되므로, 미측정은 끝까지 None으로 흘려보낸다.
    """
    raw = row.get(key, "")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    """None을 보존하며 반올림한다."""
    return None if value is None else round(value, digits)


def _broken_clip(entry: Path, exc: Exception,
                 tz_fix_before: Optional[datetime.datetime]) -> Optional[Dict[str, Any]]:
    """meta.json을 못 읽은 클립을 '손상' 항목으로 만든다.

    시각은 디렉터리명에서 복원한다. 0바이트 파일 개수를 함께 세는데, 전부
    0바이트면 클립 저장 중 전원이 끊긴 흔적이라 원인 판단이 달라진다.

    Args:
        entry: 클립 디렉터리.
        exc: meta.json 읽기 실패 예외.
        tz_fix_before: 타임존 보정 기준 시각.

    Returns:
        손상 클립 딕셔너리. 디렉터리명에서 시각조차 복원 못 하면 None.
    """
    name_match = _CLIP_NAME_RE.match(entry.name)
    if name_match is None:
        return None
    stamp = _shift(
        datetime.datetime.strptime("".join(name_match.groups()), "%Y%m%d%H%M%S"),
        tz_fix_before)
    files = [p for p in entry.iterdir() if p.is_file()]
    empty = sum(1 for p in files if p.stat().st_size == 0)
    return {
        "id": entry.name,
        "date": stamp.strftime("%Y-%m-%d"),
        "ts": stamp.isoformat(timespec="milliseconds"),
        "broken": True,
        "broken_reason": "{}: {}".format(type(exc).__name__, exc),
        "file_count": len(files),
        "empty_files": empty,
        "detections": [],
    }


def scan_clips(events_dir: Path,
               tz_fix_before: Optional[datetime.datetime]) -> List[Dict[str, Any]]:
    """이벤트 클립 디렉터리를 훑어 트리거 프레임 1장으로 정규화한다.

    meta.json 스키마가 두 가지다. 과거 클립은 프레임 배열(`frames`)을 갖고,
    최근 클립은 트리거 프레임 하나(`frame`)만 저장한다. 화면에는 항상 1장만
    보이므로, 배열인 경우 트리거 시점(offset_sec 절댓값 최소) 프레임을 고른다.

    읽을 수 없는 meta.json은 건너뛰지 않고 `broken` 항목으로 남긴다. 이 프로젝트에서
    0바이트 파일은 전원 급단의 흔적이므로, 조용히 사라지면 사고의 증거가 함께 사라진다.

    Args:
        events_dir: 클립 루트 (`<root>/events`).
        tz_fix_before: 타임존 보정 기준 시각. None이면 보정하지 않는다.

    Returns:
        클립 정보 딕셔너리 목록 (트리거 시각 오름차순).
    """
    clips: List[Dict[str, Any]] = []
    if not events_dir.is_dir():
        return clips

    for entry in sorted(events_dir.iterdir()):
        if not entry.is_dir():
            continue
        try:
            with open(entry / "meta.json", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            broken = _broken_clip(entry, exc, tz_fix_before)
            if broken is not None:
                clips.append(broken)
            continue

        frames = meta.get("frames")
        if isinstance(frames, list) and frames:
            # 트리거 시점(offset_sec == 0)에 가장 가까운 프레임이 사건 그 자체다.
            frame = min(frames, key=lambda fr: abs(float(fr.get("offset_sec", 0.0))))
            frame_count = len(frames)
        else:
            frame = meta.get("frame") or {}
            frame_count = 1 if frame else 0
        if not frame.get("file"):
            continue

        trigger = meta.get("trigger", {})
        stamp = _parse_time(trigger.get("wall_time", ""))
        if stamp is None:
            name_match = _CLIP_NAME_RE.match(entry.name)
            if name_match is None:
                continue
            stamp = datetime.datetime.strptime(
                "".join(name_match.groups()), "%Y%m%d%H%M%S")
        stamp = _shift(stamp, tz_fix_before)

        clips.append({
            "id": entry.name,
            "date": stamp.strftime("%Y-%m-%d"),
            "ts": stamp.isoformat(timespec="milliseconds"),
            "label": trigger.get("label"),
            "distance_cm": trigger.get("distance_cm"),
            "risk_level": trigger.get("risk_level"),
            "tof_only_mode": bool(trigger.get("tof_only_mode")),
            "direction": trigger.get("direction"),
            "suppressed": meta.get("suppressed_high_since_prev_clip"),
            "image": frame["file"],
            "frame_count": frame_count,
            "offset_sec": frame.get("offset_sec"),
            "raw_distance_cm": frame.get("raw_distance_cm"),
            "filtered_distance_cm": frame.get("filtered_distance_cm"),
            "detections": frame.get("detections") or [],
        })

    clips.sort(key=lambda c: c["ts"])
    return clips


class Catalog:
    """뷰어가 서빙하는 데이터 전체 (세션 + 클립).

    CSV 파싱 결과를 들고 있다가 `/api/session` 요청에 재사용한다. 새 로그를
    수집했으면 `/api/index?refresh=1`로 다시 읽는다.

    Args:
        root: 로그 루트 디렉터리 (`<root>/*.csv`, `<root>/events/`).
        tz_fix_before: 타임존 보정 기준 시각. None이면 보정하지 않는다.
    """

    def __init__(self, root: Path,
                 tz_fix_before: Optional[datetime.datetime]) -> None:
        self.root = root
        self.tz_fix_before = tz_fix_before
        self.sessions: List[analyze_logs.Session] = []
        self.clips: List[Dict[str, Any]] = []
        self._payload: Optional[Dict[str, Any]] = None

    def payload(self, refresh: bool = False) -> Dict[str, Any]:
        """캘린더·목록에 필요한 인덱스를 반환한다 (필요 시 재수집).

        Args:
            refresh: True면 캐시를 버리고 디스크를 다시 읽는다.

        Returns:
            `days`/`sessions`/`clips`/`thresholds`를 담은 딕셔너리.
        """
        if refresh or self._payload is None:
            self._load()
        return self._payload

    def _load(self) -> None:
        """디스크에서 CSV와 클립을 다시 읽어 인덱스를 만든다."""
        csv_paths = sorted(glob.glob(str(self.root / "*.csv")))
        self.sessions = analyze_logs.load_sessions(csv_paths, self.tz_fix_before)
        self.clips = scan_clips(self.root / "events", self.tz_fix_before)

        sessions_meta = [self._session_meta(i, s) for i, s in enumerate(self.sessions)]

        days: Dict[str, Dict[str, List[Any]]] = {}
        for meta in sessions_meta:
            days.setdefault(meta["date"], {"sessions": [], "clips": []})
            days[meta["date"]]["sessions"].append(meta["id"])
        for clip in self.clips:
            days.setdefault(clip["date"], {"sessions": [], "clips": []})
            days[clip["date"]]["clips"].append(clip["id"])

        self._payload = {
            "root": str(self.root),
            "generated": datetime.datetime.now().isoformat(timespec="seconds"),
            "csv_files": len(csv_paths),
            "tz_fix_before": (self.tz_fix_before.isoformat()
                              if self.tz_fix_before else None),
            "thresholds": {
                "target_fps": config.TARGET_FPS,
                "fps_fallback": config.FPS_FALLBACK_THRESHOLD,
                "throttle_temp_c": config.THERMAL_THROTTLE_TEMP_C,
                "latency_warn_ms": config.LATENCY_WARN_THRESHOLD_MS,
                "high_risk_cm": config.HIGH_RISK_DIST_CM,
                "mid_risk_cm": config.MID_RISK_DIST_CM,
                "tof_oor_cm": config.TOF_OUT_OF_RANGE_CM,
                "luma_min": config.VISION_BLIND_LUMA_MIN,
                "luma_max": config.VISION_BLIND_LUMA_MAX,
                "exposure_max": config.CSI_AE_EXPOSURE_MAX,
                "gain_max": config.CSI_AE_GAIN_MAX,
            },
            "days": days,
            "sessions": sessions_meta,
            "clips": self.clips,
        }

    def _session_meta(self, index: int,
                      session: analyze_logs.Session) -> Dict[str, Any]:
        """세션 하나의 요약(KPI 포함)을 만든다."""
        stats = analyze_logs.summarize(session)
        start, end = session.times[0], session.times[-1]
        return {
            "id": index,
            "source": os.path.basename(session.source),
            "seq": session.index,
            "date": start.strftime("%Y-%m-%d"),
            "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds"),
            "stats": {k: _round(v, 4) for k, v in stats.items()},
        }

    def series(self, index: int) -> Dict[str, Any]:
        """세션 하나의 시계열 전체를 반환한다.

        다운샘플링하지 않는다. 최대 세션도 수천 행이라 그대로 보내는 편이
        코드도 화면도 정확하다 (스파이크가 표본에서 빠지지 않는다).

        Args:
            index: `payload()["sessions"]`의 id.

        Returns:
            `t`(시작 기준 초) · `num`(수치 컬럼) · `count`(증가분 카운터) ·
            `alert`(위험 상태 0/1) · `tts`(발화한 행만 희소 배열)를 담은 딕셔너리.

        Raises:
            IndexError: 없는 세션 id일 때.
        """
        session = self.sessions[index]
        base = session.times[0]

        offsets = [round((t - base).total_seconds(), 1) for t in session.times]
        numeric: Dict[str, List[Optional[float]]] = {
            name: [_round(_cell(r, name)) for r in session.rows]
            for name in _NUMERIC_COLUMNS
        }
        # 세션 첫 행의 fps는 EMA 초기화 아티팩트(실측 예: 1164)라 y축을 통째로
        # 망가뜨린다. analyze_logs가 통계에서 빼는 것과 같은 이유로 비운다.
        if numeric["fps"]:
            numeric["fps"][0] = None

        counts = {
            name: [int(_cell(r, name) or 0) for r in session.rows]
            for name in _COUNT_COLUMNS
        }
        alerts = [1 if str(r.get("alert_triggered", "")).lower() == "true" else 0
                  for r in session.rows]
        tts = [[i, r["tts_spoken"]] for i, r in enumerate(session.rows)
               if r.get("tts_spoken")]

        return {
            "meta": self._session_meta(index, session),
            "t": offsets,
            "num": numeric,
            "count": counts,
            "alert": alerts,
            "tts": tts,
        }

    def clip_dir(self, clip_id: str) -> Optional[Path]:
        """클립 id에 대응하는 디렉터리. 인덱스에 없는 id면 None.

        경로 조작을 막기 위해 문자열을 조합하지 않고 스캔된 id 집합과 대조한다.
        """
        if any(c["id"] == clip_id for c in self.clips):
            return self.root / "events" / clip_id
        return None


class _Handler(BaseHTTPRequestHandler):
    """읽기 전용 라우팅 핸들러. `catalog`는 서버 인스턴스가 주입한다."""

    server_version = "RasEyesLogViewer/1.0"
    catalog: Catalog

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 규약)
        """GET 라우팅."""
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        try:
            if path in ("/", "/index.html"):
                self._send_html()
            elif path == "/api/index":
                self._send_json(self.catalog.payload(refresh="refresh" in query))
            elif path == "/api/session":
                self._send_session(query)
            elif path.startswith("/clip/"):
                self._send_clip_image(path)
            else:
                self._send_error(404, "없는 경로입니다: {}".format(path))
        except BrokenPipeError:
            pass  # 브라우저가 먼저 끊었다 (탭 닫기 등) — 서버 문제가 아니다.
        except Exception as exc:  # noqa: BLE001 (뷰어가 통째로 죽는 것을 막는다)
            self._send_error(500, "{}: {}".format(type(exc).__name__, exc))

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        """접근 로그를 한 줄로 줄인다 (기본 포맷은 타임스탬프가 중복된다)."""
        sys.stderr.write("  {} {}\n".format(self.address_string(), fmt % args))

    def _send_session(self, query: Dict[str, List[str]]) -> None:
        """`/api/session?id=<n>` 처리."""
        raw = (query.get("id") or [""])[0]
        try:
            index = int(raw)
        except ValueError:
            self._send_error(400, "id는 정수여야 합니다: {!r}".format(raw))
            return
        self.catalog.payload()  # 캐시가 비어 있으면 채운다.
        if not 0 <= index < len(self.catalog.sessions):
            self._send_error(404, "없는 세션 id: {}".format(index))
            return
        self._send_json(self.catalog.series(index))

    def _send_clip_image(self, path: str) -> None:
        """`/clip/<id>/<file>` 처리 (JPEG 서빙)."""
        parts = path.split("/", 3)
        if len(parts) != 4:
            self._send_error(404, "클립 경로 형식이 아닙니다: {}".format(path))
            return
        _, _, clip_id, filename = parts
        self.catalog.payload()
        directory = self.catalog.clip_dir(clip_id)
        # 파일명은 조합하지 않고 실제 디렉터리 목록과 대조한다 (경로 탈출 차단).
        if directory is None or filename not in os.listdir(directory):
            self._send_error(404, "없는 클립 파일: {}".format(path))
            return
        try:
            body = (directory / filename).read_bytes()
        except OSError as exc:
            self._send_error(404, "읽기 실패: {}".format(exc))
            return
        self._respond(200, "image/jpeg", body, cache="public, max-age=3600")

    def _send_html(self) -> None:
        """프런트엔드 HTML을 서빙한다 (요청마다 디스크에서 다시 읽는다)."""
        try:
            body = _HTML_PATH.read_bytes()
        except OSError as exc:
            self._send_error(500, "프런트엔드 파일을 못 읽었습니다: {}".format(exc))
            return
        self._respond(200, "text/html; charset=utf-8", body)

    def _send_json(self, payload: Any) -> None:
        """딕셔너리를 JSON으로 응답한다."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._respond(200, "application/json; charset=utf-8", body)

    def _send_error(self, status: int, message: str) -> None:
        """에러를 JSON으로 응답한다 (프런트엔드가 그대로 표시한다)."""
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self._respond(status, "application/json; charset=utf-8", body)

    def _respond(self, status: int, content_type: str, body: bytes,
                 cache: str = "no-store") -> None:
        """공통 응답 전송."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    """CLI 진입점.

    Returns:
        종료 코드 (0=정상 종료, 1=로그 루트 없음).
    """
    parser = argparse.ArgumentParser(description="RasEyes 운영 로그 뷰어")
    parser.add_argument("--root", default="logs_archive",
                        help="로그 루트 (<root>/*.csv, <root>/events/). 기본: logs_archive")
    parser.add_argument("--host", default="0.0.0.0",
                        help="바인드 주소. 기본 0.0.0.0 (tailscale·LAN 모두 접속 가능). "
                             "이 머신에서만 보려면 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="포트. 기본 8765")
    parser.add_argument("--tz-fix-before", default=None,
                        help="이 ISO8601 시각 이전 기록에 +1h 보정 "
                             "(Pi가 Asia/Shanghai였던 구간). 예: 2026-07-27T00:00:00")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print("로그 루트가 없습니다: {}".format(root))
        return 1

    tz_fix_before = None
    if args.tz_fix_before:
        tz_fix_before = _parse_time(args.tz_fix_before)
        if tz_fix_before is None:
            parser.error("--tz-fix-before 파싱 실패: {}".format(args.tz_fix_before))

    catalog = Catalog(root, tz_fix_before)
    index = catalog.payload()
    print("로그 루트 : {}".format(root))
    print("수집 결과 : CSV {}개 → 세션 {}개 / 클립 {}개 / 기록된 날짜 {}일".format(
        index["csv_files"], len(index["sessions"]),
        len(index["clips"]), len(index["days"])))

    handler = type("_BoundHandler", (_Handler,), {"catalog": catalog})
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    print("접속 주소 : http://{}:{}".format(
        "localhost" if args.host in ("0.0.0.0", "127.0.0.1") else args.host, args.port))
    if args.host == "0.0.0.0":
        print("            (다른 기기에서는 이 머신의 IP:{} 로 접속)".format(args.port))
    print("종료      : Ctrl+C")
    # 출력을 파일로 리다이렉트하면 블록 버퍼링이라 기동 안내가 한참 안 보인다.
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
