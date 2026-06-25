"""RKNN 추론 속도 측정 스크립트 (Orange Pi 5 전용).

CSI 카메라로 실시간 프레임을 캡처하면서 RKNN 모델 추론 latency를 측정한다.
워밍업 후 N회 추론의 평균/최소/최대/P95 지연과 FPS를 출력하고
KPI(평균 < 60ms) 달성 여부를 판정한다.

사전 조건:
    - yolov8n.rknn 파일이 프로젝트 루트에 존재할 것
    - rknnlite2 패키지 접근 가능할 것 (Armbian 시스템 패키지 또는 pip)
    - CSI 카메라(/dev/video11)가 연결되어 있을 것

사용법:
    python scripts/bench_rknn.py
    python scripts/bench_rknn.py --model yolov8n.rknn --frames 100
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass

import numpy as np

import config


_KPI_LATENCY_MS = 60.0


@dataclass
class RknnBenchResult:
    """RKNN 벤치마크 측정 결과."""

    model_path: str
    frame_count: int
    latency_mean_ms: float
    latency_min_ms: float
    latency_max_ms: float
    latency_p95_ms: float
    fps: float
    kpi_passed: bool


def run_benchmark(model_path: str, frames: int) -> RknnBenchResult:
    """RKNN 추론 성능을 측정한다.

    Args:
        model_path: .rknn 모델 파일 경로.
        frames: 측정할 프레임 수 (워밍업 제외).

    Returns:
        RknnBenchResult 측정 결과.

    Raises:
        SystemExit: 모델 파일이 없거나 RKNN/카메라 초기화 실패 시.
    """
    if not os.path.exists(model_path):
        raise SystemExit(
            f"RKNN 모델 파일이 없습니다: {model_path}\n"
            f"PC(x86)에서 변환 후 scp로 전송하세요:\n"
            f"  python scripts/export_rknn.py --no-quant\n"
            f"  scp {model_path} raseyes:~/RasEyes/"
        )

    try:
        from vision.csi_camera_hal import CSICameraHAL
        from vision.rknn_detector import RknnDetector
    except ImportError as exc:
        raise SystemExit(f"필수 패키지 미설치: {exc}") from exc

    camera = CSICameraHAL()
    detector = RknnDetector(camera=camera, model_path=model_path)

    try:
        detector.start()
    except (ImportError, RuntimeError) as exc:
        raise SystemExit(f"RKNN 초기화 실패: {exc}") from exc

    print(f"워밍업 {config.BENCHMARK_WARMUP_FRAMES}회 완료 중...")
    for _ in range(config.BENCHMARK_WARMUP_FRAMES):
        detector.get_frame_detections()
    print(f"워밍업 {config.BENCHMARK_WARMUP_FRAMES}회 완료")

    latencies: list[float] = []
    print(f"측정 중: 0/{frames}", end="", flush=True)
    for i in range(frames):
        t0 = time.perf_counter()
        detector.get_frame_detections()
        latencies.append((time.perf_counter() - t0) * 1000.0)
        print(f"\r측정 중: {i + 1}/{frames}", end="", flush=True)
    print()

    detector.stop()

    arr = np.array(latencies)
    mean_ms = float(arr.mean())
    return RknnBenchResult(
        model_path=model_path,
        frame_count=frames,
        latency_mean_ms=mean_ms,
        latency_min_ms=float(arr.min()),
        latency_max_ms=float(arr.max()),
        latency_p95_ms=float(np.percentile(arr, 95)),
        fps=1000.0 / mean_ms if mean_ms > 0 else 0.0,
        kpi_passed=mean_ms < _KPI_LATENCY_MS,
    )


def _print_report(result: RknnBenchResult) -> None:
    """벤치마크 결과를 출력한다."""
    kpi_str = f"PASS (기준: < {_KPI_LATENCY_MS:.0f}ms)" if result.kpi_passed else (
        f"FAIL (평균 {result.latency_mean_ms:.1f}ms, 기준: < {_KPI_LATENCY_MS:.0f}ms)\n"
        f"  → INT8 양자화 모델 사용 또는 입력 해상도 축소 검토"
    )
    print("\n" + "=" * 50)
    print(f"  모델             : {result.model_path}")
    print(f"  측정 프레임      : {result.frame_count}")
    print(f"  평균 지연        : {result.latency_mean_ms:.1f} ms")
    print(f"  최소 지연        : {result.latency_min_ms:.1f} ms")
    print(f"  최대 지연        : {result.latency_max_ms:.1f} ms")
    print(f"  P95 지연         : {result.latency_p95_ms:.1f} ms")
    print(f"  달성 FPS         : {result.fps:.1f}")
    print(f"  KPI 통과         : {kpi_str}")
    print("=" * 50)


def main() -> None:
    parser = argparse.ArgumentParser(description="RKNN 추론 속도 측정 (Orange Pi 5)")
    parser.add_argument(
        "--model",
        default=config.RKNN_MODEL_PATH,
        help=f"RKNN 모델 파일 경로 (기본값: {config.RKNN_MODEL_PATH})",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=50,
        help="측정 프레임 수 (기본값: 50)",
    )
    args = parser.parse_args()

    print(
        f"RKNN 벤치마크 시작 "
        f"(model={args.model}, warmup={config.BENCHMARK_WARMUP_FRAMES}, frames={args.frames})"
    )
    result = run_benchmark(model_path=args.model, frames=args.frames)
    _print_report(result)

    sys.exit(0 if result.kpi_passed else 1)


if __name__ == "__main__":
    main()
