"""ClipRecorder(경고 이벤트 클립 저장) 테스트.

ClipRecorder의 공개 메서드는 단조 시각을 인자로 받으므로, 200ms 샘플링·30초
쿨다운·7일 보존 같은 시간 의존 로직을 sleep 없이 검증한다.
"""
import json
import os
import threading
import time
from pathlib import Path

import numpy as np
import pytest

import config
from fusion.engine import FusionResult, RiskLevel
from logs.clip_recorder import ClipRecorder
from main import RasEyesApp
from vision.interface import DetectionResult


@pytest.fixture
def recorder(tmp_path) -> ClipRecorder:
    """tmp_path를 클립 루트로 쓰는 ClipRecorder."""
    return ClipRecorder(clip_dir=str(tmp_path))


@pytest.fixture
def frame() -> np.ndarray:
    """640x480 BGR 더미 프레임."""
    return np.zeros((config.FRAME_HEIGHT, config.FRAME_WIDTH, 3), dtype=np.uint8)


def _high_result(distance_cm: float = 34.0, label="chair") -> FusionResult:
    """HIGH 위험도 퓨전 결과를 만든다. label=None이면 ToF 단독 모드 경로를 흉내낸다."""
    return FusionResult(
        risk_level=RiskLevel.HIGH,
        distance_cm=distance_cm,
        tof_only_mode=label is None,
        top_label=label,
        direction="정면" if label else None,
    )


def _feed(recorder: ClipRecorder, frame: np.ndarray, start: float, end: float,
          step: float = 0.2, detections=None) -> None:
    """start부터 end까지 step 간격으로 프레임을 공급한다 (frame_ts는 now와 동일)."""
    now = start
    while now <= end + 1e-9:
        recorder.offer_frame(now, frame, now, detections or [], 90.0, 88.0)
        now = round(now + step, 6)


class TestSampling:
    def test_samples_at_buffer_fps_interval(self, recorder, frame) -> None:
        """샘플링 주기(200ms)보다 촘촘히 공급해도 주기마다 1장만 적재한다."""
        for i in range(11):  # 0.00 ~ 0.50초, 50ms 간격
            now = i * 0.05
            recorder.offer_frame(now, frame, now, [], 90.0, 88.0)
        # 0.0 / 0.2 / 0.4 → 3장
        assert len(recorder._buffer) == 3

    def test_duplicate_frame_ts_skipped(self, recorder, frame) -> None:
        """같은 frame_ts가 다시 들어오면 적재하지 않는다 (저전력 4FPS 대비)."""
        recorder.offer_frame(0.0, frame, 0.0, [], 90.0, 88.0)
        recorder.offer_frame(0.5, frame, 0.0, [], 90.0, 88.0)
        assert len(recorder._buffer) == 1

    def test_none_frame_ignored(self, recorder) -> None:
        """프레임이 아직 없을 때(None) 예외 없이 무시한다."""
        recorder.offer_frame(0.0, None, None, [], 90.0, 88.0)
        assert len(recorder._buffer) == 0

    def test_buffer_holds_only_pre_window(self, recorder, frame) -> None:
        """링 버퍼는 CLIP_PRE_SEC 분량(=BUFFER_FPS x PRE_SEC)만 유지한다."""
        expected = int(config.CLIP_BUFFER_FPS * config.CLIP_PRE_SEC)
        _feed(recorder, frame, 0.0, 20.0)
        assert len(recorder._buffer) == expected


class TestTriggerAndDump:
    def test_dump_writes_trigger_frame_and_meta(self, recorder, frame, tmp_path) -> None:
        """HIGH 트리거 시 트리거 순간 프레임 1장과 meta.json만 기록된다."""
        detections = [DetectionResult(label="chair", confidence=0.82, bbox=(10, 20, 110, 220))]
        _feed(recorder, frame, 0.0, 5.0, detections=detections)
        assert recorder.trigger(5.0, _high_result()) is True
        _feed(recorder, frame, 5.2, 5.0 + config.CLIP_POST_SEC, detections=detections)
        recorder.close()

        dirs = [p for p in tmp_path.iterdir() if p.is_dir()]
        assert len(dirs) == 1
        clip = dirs[0]
        assert clip.name.endswith("_chair_34cm")

        meta = json.loads((clip / "meta.json").read_text(encoding="utf-8"))
        jpgs = sorted(clip.glob("*.jpg"))
        assert [p.name for p in jpgs] == ["trigger.jpg"]
        assert jpgs[0].stat().st_size > 0

        assert meta["trigger"]["risk_level"] == "HIGH"
        assert meta["trigger"]["label"] == "chair"
        assert meta["trigger"]["tof_only_mode"] is False
        # 저장 대상은 트리거 시점 이전(또는 동시)에 찍힌 링 버퍼의 마지막 프레임
        assert meta["frame"]["file"] == "trigger.jpg"
        assert meta["frame"]["offset_sec"] <= 0

    def test_meta_has_both_tof_distances_and_detections(self, recorder, frame, tmp_path) -> None:
        """프레임 메타에 원시/필터 ToF 거리와 탐지 결과(label·bbox·confidence)가 담긴다."""
        detections = [DetectionResult(label="person", confidence=0.9, bbox=(1, 2, 3, 4))]
        recorder.offer_frame(0.0, frame, 0.0, detections, 95.5, 91.25)
        recorder.trigger(0.0, _high_result())
        _feed(recorder, frame, 0.2, config.CLIP_POST_SEC, detections=detections)
        recorder.close()

        clip = next(p for p in tmp_path.iterdir() if p.is_dir())
        meta = json.loads((clip / "meta.json").read_text(encoding="utf-8"))
        first = meta["frame"]
        assert first["raw_distance_cm"] == pytest.approx(95.5)
        assert first["filtered_distance_cm"] == pytest.approx(91.25)
        assert first["detections"][0]["label"] == "person"
        assert first["detections"][0]["bbox"] == [1, 2, 3, 4]
        assert first["detections"][0]["confidence"] == pytest.approx(0.9)

    def test_tof_only_high_uses_fallback_label(self, recorder, frame, tmp_path) -> None:
        """top_label이 없는 ToF 단독 HIGH는 폴더명에 'obstacle'을 쓴다."""
        recorder.offer_frame(0.0, frame, 0.0, [], 50.0, 50.0)
        recorder.trigger(0.0, _high_result(distance_cm=50.0, label=None))
        _feed(recorder, frame, 0.2, config.CLIP_POST_SEC)
        recorder.close()

        clip = next(p for p in tmp_path.iterdir() if p.is_dir())
        assert clip.name.endswith("_obstacle_50cm")

    def test_dump_proceeds_when_frames_stop(self, recorder, frame, tmp_path) -> None:
        """후속 수집 중 프레임이 끊겨도 deadline이 지나면 덤프된다."""
        recorder.offer_frame(0.0, frame, 0.0, [], 90.0, 88.0)
        recorder.trigger(0.0, _high_result())
        # 프레임 없이 시간만 흐른 상태로 호출
        recorder.offer_frame(config.CLIP_POST_SEC + 1.0, None, None, [], 90.0, 88.0)
        recorder.close()

        assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 1

    def test_pending_clip_discarded_on_close(self, recorder, frame, tmp_path) -> None:
        """후속 수집이 끝나기 전 close()되면 미완 클립은 폐기된다."""
        recorder.offer_frame(0.0, frame, 0.0, [], 90.0, 88.0)
        recorder.trigger(0.0, _high_result())
        recorder.close()

        assert [p for p in tmp_path.iterdir() if p.is_dir()] == []


class TestCooldown:
    def test_repeat_high_suppressed_within_cooldown(self, recorder, frame) -> None:
        """쿨다운 중 HIGH는 새 클립을 만들지 않고 억제 카운터만 증가시킨다."""
        recorder.offer_frame(0.0, frame, 0.0, [], 90.0, 88.0)
        assert recorder.trigger(0.0, _high_result()) is True
        assert recorder.trigger(1.0, _high_result()) is False
        assert recorder.trigger(config.CLIP_COOLDOWN_SEC - 0.1, _high_result()) is False
        assert recorder._suppressed == 2

    def test_trigger_allowed_after_cooldown(self, recorder, frame) -> None:
        """쿨다운이 지나면 다시 트리거된다."""
        recorder.offer_frame(0.0, frame, 0.0, [], 90.0, 88.0)
        assert recorder.trigger(0.0, _high_result()) is True

        later = config.CLIP_COOLDOWN_SEC
        recorder.offer_frame(later, frame, later, [], 90.0, 88.0)
        assert recorder.trigger(later, _high_result()) is True

    def test_empty_buffer_does_not_consume_cooldown(self, recorder, frame) -> None:
        """저장할 프레임이 없어 실패한 트리거는 쿨다운을 소모하지 않는다.

        소모하면 카메라가 잠깐 멈춘 사이의 HIGH 하나가 뒤이은 30초를 통째로
        막아, 실제 위험 순간의 클립이 남지 않는다.
        """
        assert recorder.trigger(0.0, _high_result()) is False
        assert recorder._suppressed == 1  # 놓친 HIGH도 집계에는 남는다

        recorder.offer_frame(0.1, frame, 0.1, [], 90.0, 88.0)
        assert recorder.trigger(0.1, _high_result()) is True

    def test_suppressed_count_recorded_in_next_clip(self, recorder, frame, tmp_path) -> None:
        """억제된 HIGH 횟수가 다음 클립 meta에 기록되고 카운터는 초기화된다."""
        base = 0.0
        recorder.offer_frame(base, frame, base, [], 90.0, 88.0)
        recorder.trigger(base, _high_result(distance_cm=34.0))
        _feed(recorder, frame, base + 0.2, base + config.CLIP_POST_SEC)

        recorder.trigger(base + config.CLIP_POST_SEC + 1.0, _high_result())  # 억제됨

        second = config.CLIP_COOLDOWN_SEC + 1.0
        recorder.trigger(second, _high_result(distance_cm=55.0))
        _feed(recorder, frame, second + 0.2, second + config.CLIP_POST_SEC)
        recorder.close()

        clips = sorted(p for p in tmp_path.iterdir() if p.is_dir())
        assert len(clips) == 2
        metas = {p.name: json.loads((p / "meta.json").read_text(encoding="utf-8")) for p in clips}
        first_meta = next(m for n, m in metas.items() if n.endswith("_34cm"))
        second_meta = next(m for n, m in metas.items() if n.endswith("_55cm"))
        assert first_meta["suppressed_high_since_prev_clip"] == 0
        assert second_meta["suppressed_high_since_prev_clip"] == 1


class TestRotation:
    def _make_clip_dirs(self, root: Path, count: int, age_days: float = 0.0) -> None:
        """더미 클립 폴더를 만들고 mtime을 age_days만큼 과거로 돌린다."""
        stamp = time.time() - age_days * 86400.0
        for i in range(count):
            path = root / "2026072{}_{:06d}_chair_50cm".format(i % 10, i)
            path.mkdir()
            (path / "meta.json").write_text("{}", encoding="utf-8")
            os.utime(path, (stamp - i, stamp - i))

    def test_removes_over_count_limit(self, recorder, tmp_path) -> None:
        """개수 한도를 넘으면 오래된 폴더부터 삭제된다."""
        self._make_clip_dirs(tmp_path, config.CLIP_RETENTION_MAX_DIRS + 3)
        recorder.rotate()
        assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == config.CLIP_RETENTION_MAX_DIRS

    def test_removes_over_age_limit(self, recorder, tmp_path) -> None:
        """보존 기간을 넘긴 폴더는 개수 한도 이내여도 삭제된다."""
        self._make_clip_dirs(tmp_path, 2, age_days=config.CLIP_RETENTION_MAX_AGE_DAYS + 1)
        recorder.rotate()
        assert [p for p in tmp_path.iterdir() if p.is_dir()] == []

    def test_keeps_recent_clips(self, recorder, tmp_path) -> None:
        """한도 이내의 최근 폴더는 보존한다."""
        self._make_clip_dirs(tmp_path, 3, age_days=1.0)
        recorder.rotate()
        assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 3

    def test_rotate_on_missing_dir_is_noop(self, tmp_path) -> None:
        """클립 루트가 아직 없어도 예외 없이 통과한다 (프로세스 시작 시 호출)."""
        ClipRecorder(clip_dir=str(tmp_path / "nope")).rotate()


class TestMockModeIntegration:
    def test_high_alert_writes_clip_in_mock_mode(self, tmp_path, monkeypatch) -> None:
        """Mock 모드 메인 루프에서 HIGH가 지속되면 실제로 클립이 저장된다."""
        # 테스트 실행 시간을 줄이기 위해 전/후 구간을 짧게 조정한다.
        monkeypatch.setattr(config, "CLIP_PRE_SEC", 1.0)
        monkeypatch.setattr(config, "CLIP_POST_SEC", 0.5)

        app = RasEyesApp(use_mock=True)
        app._clips = ClipRecorder(clip_dir=str(tmp_path))
        app._sensor.set_distance(30.0)  # HIGH 유발 (ToF 단독 모드)
        app.start()

        loop = threading.Thread(target=app._run_loop, daemon=True)
        loop.start()
        time.sleep(3.0)
        app._stop_event.set()
        loop.join(timeout=5.0)
        app.stop()

        clips = [p for p in tmp_path.iterdir() if p.is_dir()]
        assert len(clips) == 1, "HIGH 경보가 지속됐는데 클립이 저장되지 않음"
        meta = json.loads((clips[0] / "meta.json").read_text(encoding="utf-8"))
        assert meta["trigger"]["risk_level"] == "HIGH"
        assert [p.name for p in clips[0].glob("*.jpg")] == ["trigger.jpg"]
        assert meta["frame"]["file"] == "trigger.jpg"
