"""RknnDetector NPU 추론 성능 벤치마크 (Orange Pi 5, RK3588S NPU 전용).

사용법:
    python -m tests.benchmark_rknn
    python -m tests.benchmark_rknn --frames 100
    python -m tests.benchmark_rknn --model /path/to/model.rknn --source /path/to/image.jpg

출력:
    NPU 모델·총 프레임·평균/최소/최대/P95 지연(ms)·FPS·KPI 통과 여부
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import config
from vision.rknn_detector import RknnDetector
from vision.mock_camera import MockCamera


@dataclass
class BenchmarkResult:
    """벤치마크 측정 결과."""

    model_path: str
    frame_count: int
    latency_mean_ms: float
    latency_min_ms: float
    latency_max_ms: float
    latency_p95_ms: float
    fps: float
    kpi_passed: bool  # mean < 60ms AND fps >= 15


def run_benchmark(
    frames: int = 50,
    model_path: str = config.RKNN_MODEL_PATH,
    source: str | None = None,
) -> BenchmarkResult:
    """RknnDetector NPU 추론 성능을 측정한다.

    Args:
        frames: 측정할 프레임 수 (워밍업 제외).
        model_path: .rknn 모델 파일 경로.
        source: MockCamera에 전달할 이미지 경로. None이면 빈 프레임 사용.

    Returns:
        BenchmarkResult 측정 결과.

    Raises:
        ImportError: rknnlite2 미설치 시.
        FileNotFoundError: .rknn 모델 파일 미존재 시.
    """
    if not Path(model_path).exists():
        raise FileNotFoundError(
            f".rknn 모델 파일이 없습니다: {model_path}\n"
            "GitHub Actions 아티팩트를 다운로드한 뒤 scp로 전송하세요."
        )

    camera = MockCamera(source=source)
    detector = RknnDetector(camera=camera, model_path=model_path)
    try:
        detector.start()

        # 워밍업 (NPU 런타임 초기 지연 제거)
        for _ in range(config.BENCHMARK_WARMUP_FRAMES):
            detector.get_frame_detections()

        # 측정
        latencies: list[float] = []
        for _ in range(frames):
            t0 = time.perf_counter()
            detector.get_frame_detections()
            latencies.append((time.perf_counter() - t0) * 1000.0)
    finally:
        detector.stop()
    assert detector._rknn is None, "stop() 후 RKNN 런타임이 해제되지 않았습니다."

    arr = np.array(latencies)
    mean_ms = float(arr.mean())
    return BenchmarkResult(
        model_path=model_path,
        frame_count=frames,
        latency_mean_ms=mean_ms,
        latency_min_ms=float(arr.min()),
        latency_max_ms=float(arr.max()),
        latency_p95_ms=float(np.percentile(arr, 95)),
        fps=1000.0 / mean_ms if mean_ms > 0 else 0.0,
        kpi_passed=mean_ms < 60.0 and (1000.0 / mean_ms) >= 15.0,
    )


def _rknn_available() -> bool:
    """rknnlite2 설치 및 .rknn 모델 파일 존재 여부를 확인한다."""
    try:
        from rknnlite.api import RKNNLite  # noqa: F401
        return Path(config.RKNN_MODEL_PATH).exists()
    except ImportError:
        return False


# pytest 통합 — rknnlite2 미설치 또는 모델 미존재 시 자동 skip
try:
    import pytest

    @pytest.mark.skipif(not _rknn_available(), reason="rknnlite2 미설치 또는 yolov8n.rknn 없음")
    def test_npu_kpi_latency() -> None:
        """RKNN NPU 추론이 Phase 4 KPI를 달성하는지 검증한다."""
        result = run_benchmark(frames=50)
        assert result.latency_mean_ms < 60.0, (
            f"추론 지연 KPI 미달: {result.latency_mean_ms:.1f}ms (기준 < 60ms)"
        )
        assert result.fps >= 15.0, (
            f"FPS KPI 미달: {result.fps:.1f} (기준 >= 15 FPS)"
        )

    @pytest.mark.skipif(not _rknn_available(), reason="rknnlite2 미설치 또는 yolov8n.rknn 없음")
    def test_stop_releases_rknn() -> None:
        """detector.stop() 호출 후 RKNN 런타임이 해제되는지 검증한다."""
        camera = MockCamera()
        detector = RknnDetector(camera=camera)
        detector.start()
        assert detector._rknn is not None
        detector.stop()
        assert detector._rknn is None, "detector.stop() 후 RKNN 런타임이 해제되어야 합니다."

except ImportError:
    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RknnDetector NPU 추론 성능 벤치마크")
    parser.add_argument("--frames", type=int, default=50, help="측정 프레임 수 (기본값: 50)")
    parser.add_argument(
        "--model",
        type=str,
        default=config.RKNN_MODEL_PATH,
        help=f"RKNN 모델 파일 경로 (기본값: {config.RKNN_MODEL_PATH})",
    )
    parser.add_argument("--source", type=str, default=None, help="입력 이미지 경로 (기본값: 빈 프레임)")
    args = parser.parse_args()

    print(f"\n벤치마크 시작 — frames={args.frames}, model={args.model}")
    result = run_benchmark(frames=args.frames, model_path=args.model, source=args.source)

    print("\n" + "=" * 50)
    print(f"  NPU 모델         : {result.model_path}")
    print(f"  측정 프레임      : {result.frame_count}")
    print(f"  평균 지연        : {result.latency_mean_ms:.1f} ms")
    print(f"  최소 지연        : {result.latency_min_ms:.1f} ms")
    print(f"  최대 지연        : {result.latency_max_ms:.1f} ms")
    print(f"  P95 지연         : {result.latency_p95_ms:.1f} ms")
    print(f"  달성 FPS         : {result.fps:.1f}")
    print(f"  KPI 통과         : {'✓ 통과' if result.kpi_passed else '✗ 미달'}")
    print(f"  (기준: 지연 < 60ms, FPS >= 15)")
    print("=" * 50)
