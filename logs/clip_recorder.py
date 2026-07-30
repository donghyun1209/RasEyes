"""HIGH 경보 전후 프레임을 JPEG 시퀀스로 저장하는 이벤트 클립 레코더.

오탐지 분석·유지보수를 위해 HIGH 경보 발생 시점 전후 구간만 저장한다
(상시 녹화 금지 — 저장소 수명·성능 KPI·프라이버시 고려).

설계 요점:
    - 링 버퍼는 프레임 카운트가 아닌 **시간 기준**(CLIP_BUFFER_FPS)으로 적재한다.
      저전력 모드(4FPS)에서 프레임 카운트 기준을 쓰면 적재량이 폭락하기 때문이다.
    - mp4 인코딩 없이 프레임별 JPEG만 저장한다. Pi 실측 3.15ms/프레임이라
      메인 루프에서 즉시 인코딩해도 15FPS 프레임 예산(66ms)에 영향이 없다.
    - 디스크 기록만 백그라운드 스레드에 위임한다.
    - 모든 공개 메서드는 단조 시각(`now`)을 인자로 받는다. 메인 루프가 이미
      계산해둔 값을 재사용하며, 테스트에서 sleep 없이 시간을 조작할 수 있다.

주의:
    링 버퍼와 상태 필드는 메인 루프 스레드만 조작하므로 락이 필요 없다.
    덤프 스레드에는 프레임 리스트의 소유권을 넘긴 뒤 다시 건드리지 않는다.
"""
import dataclasses
import datetime
import json
import logging
import math
import shutil
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

import cv2
import numpy as np

import config
from fusion.engine import FusionResult
from vision.interface import DetectionResult

logger = logging.getLogger(__name__)

#: top_label이 없는 HIGH(ToF 단독 모드)일 때 폴더명에 사용할 기본 레이블.
_UNKNOWN_LABEL = "obstacle"


class ClipRecorder:
    """HIGH 경보 전후 프레임을 링 버퍼에 모아 디스크에 덤프한다.

    Args:
        clip_dir: 클립 폴더를 생성할 루트 경로.
    """

    def __init__(self, clip_dir: str = config.CLIP_DIR) -> None:
        self._clip_dir = clip_dir
        self._sample_interval = 1.0 / config.CLIP_BUFFER_FPS
        self._buffer: Deque[Dict[str, Any]] = deque(
            maxlen=int(config.CLIP_BUFFER_FPS * config.CLIP_PRE_SEC)
        )
        self._last_sample_at: float = -math.inf
        self._last_frame_ts: Optional[float] = None
        self._last_trigger_at: float = -math.inf
        self._pending: Optional[Dict[str, Any]] = None
        self._suppressed: int = 0
        self._dump_thread: Optional[threading.Thread] = None

    def offer_frame(
        self,
        now: float,
        frame: Optional[np.ndarray],
        frame_ts: Optional[float],
        detections: List[DetectionResult],
        raw_distance_cm: float,
        filtered_distance_cm: float,
    ) -> None:
        """현재 프레임을 링 버퍼(및 후속 수집 중이면 클립)에 적재한다.

        샘플링 주기(CLIP_BUFFER_FPS)가 지나지 않았거나 직전과 동일한 프레임이면
        아무것도 하지 않는다. 저전력 모드(4FPS = 250ms)에서는 샘플링 주기(200ms)와
        프레임 도착 주기가 어긋나 같은 프레임이 두 번 전달될 수 있다.

        Args:
            now: 현재 단조 시각 (time.monotonic()).
            frame: 원본 BGR 프레임. None이면 적재를 건너뛴다.
            frame_ts: 프레임 캡처 단조 시각. 중복 프레임 판별에 사용.
            detections: 이 프레임의 탐지 결과 목록.
            raw_distance_cm: ToF 원시 거리 (cm). 필터 오작동 판별용.
            filtered_distance_cm: 이동평균 필터 적용 후 거리 (cm).
        """
        # 프레임이 끊겨도 진행 중인 클립이 마무리되도록 먼저 검사한다.
        self._check_pending_deadline(now)

        if frame is None or frame_ts is None:
            return
        if frame_ts == self._last_frame_ts:
            return
        if now - self._last_sample_at < self._sample_interval:
            return

        self._last_frame_ts = frame_ts
        self._last_sample_at = now

        item = self._encode_frame(now, frame, detections, raw_distance_cm, filtered_distance_cm)
        if item is None:
            return

        self._buffer.append(item)
        if self._pending is not None:
            self._pending["frames"].append(item)
            self._check_pending_deadline(now)

    def trigger(self, now: float, result: FusionResult) -> bool:
        """HIGH 경보 시점의 프레임 1장만 저장하도록 트리거한다.

        쿨다운(CLIP_COOLDOWN_SEC) 중이면 억제 카운터만 증가시킨다. 억제 횟수는
        다음 클립의 `suppressed_high_since_prev_clip`에 기록된다.

        Args:
            now: 현재 단조 시각 (time.monotonic()).
            result: HIGH로 판정된 퓨전 결과.

        Returns:
            True이면 새 클립을 저장했고, False이면 쿨다운으로 억제되었다.
        """
        if now - self._last_trigger_at < config.CLIP_COOLDOWN_SEC:
            self._suppressed += 1
            return False

        self._last_trigger_at = now
        label = result.top_label or _UNKNOWN_LABEL

        # 트리거 순간의 프레임은 링 버퍼의 가장 최근 항목
        trigger_frame = list(self._buffer)[-1] if self._buffer else None
        if trigger_frame is None:
            logger.warning("이벤트 클립: 버퍼가 비어있음 — 프레임 저장 불가")
            return False

        self._pending = {
            "frames": [trigger_frame],
            "deadline": now,
            "trigger_now": now,
            "dir_name": "{}_{}_{}cm".format(
                datetime.datetime.now().strftime("%Y%m%d_%H%M%S"),
                _sanitize(label),
                int(round(result.distance_cm)),
            ),
            "meta": {
                "wall_time": datetime.datetime.now().isoformat(timespec="milliseconds"),
                "label": label,
                "distance_cm": round(result.distance_cm, 2),
                "risk_level": result.risk_level.name,
                "tof_only_mode": result.tof_only_mode,
                "direction": result.direction,
            },
            "suppressed_high_since_prev_clip": self._suppressed,
        }
        self._suppressed = 0
        logger.info("이벤트 클립 트리거: %s (트리거 순간 프레임만)",
                    self._pending["dir_name"])
        return True

    def rotate(self) -> None:
        """보존 한도를 넘은 클립 폴더를 삭제한다.

        개수(CLIP_RETENTION_MAX_DIRS)와 기간(CLIP_RETENTION_MAX_AGE_DAYS) 중
        하나만 넘어도 삭제 대상이다. 프로세스 시작 시 1회와 덤프 직후에 호출한다
        (기간 한도는 아무 일이 없어도 넘으므로 덤프 시점 검사만으로는 부족).
        """
        root = Path(self._clip_dir)
        if not root.is_dir():
            return
        try:
            dirs = [p for p in root.iterdir() if p.is_dir()]
        except OSError as exc:
            logger.error("클립 디렉터리 조회 실패: %s", exc)
            return

        cutoff = time.time() - config.CLIP_RETENTION_MAX_AGE_DAYS * 86400.0
        survivors = []
        for path in dirs:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                self._remove_dir(path, "보존 기간 초과")
            else:
                survivors.append((mtime, path))

        survivors.sort(key=lambda item: item[0], reverse=True)
        for _, path in survivors[config.CLIP_RETENTION_MAX_DIRS:]:
            self._remove_dir(path, "보존 개수 초과")

    def close(self) -> None:
        """진행 중인 덤프를 기다리고 미완 클립을 폐기한다."""
        if self._pending is not None:
            logger.info("미완 이벤트 클립 폐기: %s", self._pending["dir_name"])
            self._pending = None
        if self._dump_thread is not None:
            self._dump_thread.join(timeout=5.0)
            self._dump_thread = None

    def _check_pending_deadline(self, now: float) -> None:
        """후속 수집 구간이 끝났으면 덤프를 시작한다."""
        if self._pending is not None and now >= self._pending["deadline"]:
            pending, self._pending = self._pending, None
            self._start_dump(pending)

    def _encode_frame(
        self,
        now: float,
        frame: np.ndarray,
        detections: List[DetectionResult],
        raw_distance_cm: float,
        filtered_distance_cm: float,
    ) -> Optional[Dict[str, Any]]:
        """프레임을 JPEG로 인코딩하고 메타데이터와 함께 버퍼 항목을 만든다."""
        try:
            ok, encoded = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), config.CLIP_JPEG_QUALITY]
            )
        except cv2.error as exc:
            logger.error("클립 프레임 인코딩 실패: %s", exc)
            return None
        if not ok:
            logger.error("클립 프레임 인코딩 실패 (imencode 반환값 False)")
            return None

        return {
            "jpeg": encoded.tobytes(),
            "mono_ts": now,
            "wall_time": datetime.datetime.now().isoformat(timespec="milliseconds"),
            "raw_distance_cm": round(raw_distance_cm, 2),
            "filtered_distance_cm": round(filtered_distance_cm, 2),
            "detections": [dataclasses.asdict(d) for d in detections],
        }

    def _start_dump(self, pending: Dict[str, Any]) -> None:
        """수집이 끝난 클립을 백그라운드 스레드로 디스크에 기록한다."""
        if self._dump_thread is not None and self._dump_thread.is_alive():
            # 쿨다운(30s) > 후속 수집(3s) + 기록 시간이라 사실상 발생하지 않지만,
            # 겹치면 폴더가 뒤섞이므로 이전 기록이 끝날 때까지 기다린다.
            self._dump_thread.join(timeout=5.0)
        self._dump_thread = threading.Thread(
            target=self._dump, args=(pending,), daemon=True, name="clip-dump"
        )
        self._dump_thread.start()

    def _dump(self, pending: Dict[str, Any]) -> None:
        """트리거 순간 프레임 1장과 meta.json을 기록한 뒤 rotation을 수행한다."""
        try:
            target = Path(self._clip_dir) / pending["dir_name"]
            target.mkdir(parents=True, exist_ok=True)

            trigger_now = pending["trigger_now"]
            frame_meta: List[Dict[str, Any]] = []
            for item in pending["frames"]:
                name = "trigger.jpg"
                (target / name).write_bytes(item["jpeg"])
                frame_meta.append(
                    {
                        "file": name,
                        "wall_time": item["wall_time"],
                        "offset_sec": round(item["mono_ts"] - trigger_now, 3),
                        "raw_distance_cm": item["raw_distance_cm"],
                        "filtered_distance_cm": item["filtered_distance_cm"],
                        "detections": item["detections"],
                    }
                )

            meta = {
                "trigger": pending["meta"],
                "suppressed_high_since_prev_clip": pending["suppressed_high_since_prev_clip"],
                "frame": frame_meta[0] if frame_meta else None,
            }
            (target / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.info("이벤트 클립 저장: %s", target)
        except Exception as exc:  # 클립 저장 실패가 장애물 경고 본기능을 죽이면 안 된다
            logger.error("이벤트 클립 저장 실패: %s", exc)

        try:
            self.rotate()
        except Exception as exc:
            logger.error("이벤트 클립 rotation 실패: %s", exc)

    @staticmethod
    def _remove_dir(path: Path, reason: str) -> None:
        """클립 폴더 하나를 삭제한다. 실패해도 예외를 전파하지 않는다."""
        try:
            shutil.rmtree(path)
            logger.info("이벤트 클립 삭제 (%s): %s", reason, path.name)
        except OSError as exc:
            logger.error("이벤트 클립 삭제 실패 (%s): %s", path.name, exc)


def _sanitize(label: str) -> str:
    """레이블을 폴더명에 쓸 수 있게 공백/구분자를 치환한다."""
    safe = "".join(ch if ch.isalnum() else "-" for ch in label).strip("-")
    return safe or _UNKNOWN_LABEL
