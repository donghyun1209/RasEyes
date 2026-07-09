"""YoloDetector 추론 성능 벤치마크.

사용법:
    python -m tests.benchmark_vision
    python -m tests.benchmark_vision --frames 200 --device mps
    python -m tests.benchmark_vision --frames 100 --source path/to/image.jpg

출력:
    디바이스·총 프레임·평균/최소/최대/P95 지연(ms)·FPS·KPI 통과 여부
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

import config
from vision.yolo_detector_hal import YoloDetector
from vision.mock_camera import MockCamera


@dataclass
class BenchmarkResult:
    """벤치마크 측정 결과."""

    device: str
    frame_count: int
    latency_mean_ms: float
    latency_min_ms: float
    latency_max_ms: float
    latency_p95_ms: float
    fps: float
    kpi_passed: bool  # mean < 60ms AND fps >= 15


def run_benchmark(
    frames: int = 100,
    device: Optional[str] = None,
    source: Optional[str] = None,
) -> BenchmarkResult:
    """YoloDetector 추론 성능을 측정한다.

    Args:
        frames: 측정할 프레임 수 (워밍업 제외).
        device: 추론 디바이스. None이면 자동 선택.
        source: MockCamera에 전달할 이미지 경로. None이면 빈 프레임 사용.

    Returns:
        BenchmarkResult 측정 결과.
    """
    camera = MockCamera(source=source)
    detector = YoloDetector(camera=camera, device=device)
    detector.start()

    # 워밍업 (MPS JIT 컴파일 지연 제거)
    for _ in range(config.BENCHMARK_WARMUP_FRAMES):
        detector.get_frame_detections()

    # 측정
    latencies: list[float] = []
    for _ in range(frames):
        t0 = time.perf_counter()
        detector.get_frame_detections()
        latencies.append((time.perf_counter() - t0) * 1000.0)

    detector.stop()
    assert detector._model is None, "stop() 후 모델이 VRAM/메모리에서 해제되지 않았습니다."

    arr = np.array(latencies)
    mean_ms = float(arr.mean())
    return BenchmarkResult(
        device=detector.device,
        frame_count=frames,
        latency_mean_ms=mean_ms,
        latency_min_ms=float(arr.min()),
        latency_max_ms=float(arr.max()),
        latency_p95_ms=float(np.percentile(arr, 95)),
        fps=1000.0 / mean_ms if mean_ms > 0 else 0.0,
        kpi_passed=mean_ms < 60.0 and (1000.0 / mean_ms) >= 15.0,
    )


def _yolo_available() -> bool:
    """yolov8n.pt 로컬 존재 또는 ultralytics 임포트 가능 여부를 확인한다."""
    try:
        import ultralytics  # noqa: F401
        return True
    except ImportError:
        return False


# pytest 통합 — 모델 파일 없으면 자동 skip
try:
    import pytest

    @pytest.mark.skipif(not _yolo_available(), reason="ultralytics 미설치")
    def test_mps_kpi_latency() -> None:
        """MPS 추론이 Phase 2 KPI를 달성하는지 검증한다."""
        result = run_benchmark(frames=50, device=None)
        assert result.latency_mean_ms < 60.0, (
            f"추론 지연 KPI 미달: {result.latency_mean_ms:.1f}ms (기준 < 60ms)"
        )
        assert result.fps >= 15.0, (
            f"FPS KPI 미달: {result.fps:.1f} (기준 >= 15 FPS)"
        )

    @pytest.mark.skipif(not _yolo_available(), reason="ultralytics 미설치")
    def test_stop_releases_model() -> None:
        """detector.stop() 호출 후 모델이 VRAM/메모리에서 해제되는지 검증한다."""
        camera = MockCamera()
        detector = YoloDetector(camera=camera)
        detector.start()
        assert detector._model is not None
        detector.stop()
        assert detector._model is None, "detector.stop() 후 모델이 메모리에서 해제되어야 합니다."

except ImportError:
    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YoloDetector 추론 성능 벤치마크")
    parser.add_argument("--frames", type=int, default=100, help="측정 프레임 수 (기본값: 100)")
    parser.add_argument("--device", type=str, default=None, help="추론 디바이스 (mps/cpu/cuda, 기본값: 자동)")
    parser.add_argument("--source", type=str, default=None, help="입력 이미지 경로 (기본값: 빈 프레임)")
    args = parser.parse_args()

    print(f"\n벤치마크 시작 — frames={args.frames}, device={args.device or '자동'}")
    result = run_benchmark(frames=args.frames, device=args.device, source=args.source)

    print("\n" + "=" * 50)
    print(f"  디바이스        : {result.device}")
    print(f"  측정 프레임     : {result.frame_count}")
    print(f"  평균 지연       : {result.latency_mean_ms:.1f} ms")
    print(f"  최소 지연       : {result.latency_min_ms:.1f} ms")
    print(f"  최대 지연       : {result.latency_max_ms:.1f} ms")
    print(f"  P95 지연        : {result.latency_p95_ms:.1f} ms")
    print(f"  달성 FPS        : {result.fps:.1f}")
    print(f"  KPI 통과        : {'✓ 통과' if result.kpi_passed else '✗ 미달'}")
    print(f"  (기준: 지연 < 60ms, FPS >= 15)")
    print("=" * 50)
