"""YOLOv8n → RKNN INT8 변환 스크립트 (PC x86에서 실행).

사전 조건:
  - pip install rknn-toolkit2  (Rockchip 공식 x86 패키지)
  - pip install ultralytics
  - dataset.txt: 양자화 보정용 대표 이미지 경로 목록 (100~300장 권장)

사용법:
  python scripts/export_rknn.py [--model yolov8n.pt] [--output yolov8n.rknn]
                                [--dataset dataset.txt] [--no-quant]

Orange Pi 5 전송:
  scp yolov8n.rknn raseyes:~/RasEyes/
"""
import argparse
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_RKNN_INPUT_SIZE = 640
_TARGET_PLATFORM = "rk3588"


def _export_onnx(model_path: str, onnx_path: str) -> None:
    """YOLOv8 .pt 파일을 ONNX로 내보낸다."""
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit("ultralytics 패키지가 필요합니다: pip install ultralytics") from exc

    logger.info("ONNX 내보내기 시작: %s → %s", model_path, onnx_path)
    model = YOLO(model_path)
    model.export(
        format="onnx",
        imgsz=_RKNN_INPUT_SIZE,
        simplify=True,
        opset=12,
    )
    # ultralytics는 모델명.onnx로 저장함
    generated = model_path.replace(".pt", ".onnx")
    if generated != onnx_path and os.path.exists(generated):
        os.rename(generated, onnx_path)
    if not os.path.exists(onnx_path):
        raise FileNotFoundError(f"ONNX 변환 실패: {onnx_path} 를 찾을 수 없습니다.")
    logger.info("ONNX 내보내기 완료: %s", onnx_path)


def _convert_rknn(
    onnx_path: str,
    output_path: str,
    dataset_path: str,
    do_quantization: bool,
) -> None:
    """ONNX를 RKNN INT8 포맷으로 변환한다."""
    try:
        from rknn.api import RKNN
    except ImportError as exc:
        raise SystemExit(
            "rknn-toolkit2가 필요합니다 (x86 전용):\n"
            "  pip install rknn-toolkit2\n"
            "Orange Pi 5에는 설치하지 마세요."
        ) from exc

    rknn = RKNN(verbose=True)

    logger.info("RKNN 설정 (platform=%s)", _TARGET_PLATFORM)
    rknn.config(
        mean_values=[[0, 0, 0]],
        std_values=[[255, 255, 255]],
        target_platform=_TARGET_PLATFORM,
        quantization_algorithm="normal",
    )

    logger.info("ONNX 로드: %s", onnx_path)
    ret = rknn.load_onnx(model=onnx_path)
    if ret != 0:
        raise RuntimeError(f"ONNX 로드 실패 (ret={ret})")

    logger.info("모델 빌드 (do_quantization=%s)", do_quantization)
    if do_quantization:
        if not os.path.exists(dataset_path):
            raise FileNotFoundError(
                f"양자화 데이터셋 파일이 없습니다: {dataset_path}\n"
                "각 줄에 이미지 경로를 하나씩 적은 텍스트 파일을 준비하세요."
            )
        ret = rknn.build(do_quantization=True, dataset=dataset_path)
    else:
        logger.warning("양자화 비활성화 — FP16 모드로 빌드합니다.")
        ret = rknn.build(do_quantization=False)

    if ret != 0:
        raise RuntimeError(f"RKNN 빌드 실패 (ret={ret})")

    logger.info("RKNN 내보내기: %s", output_path)
    ret = rknn.export_rknn(output_path)
    if ret != 0:
        raise RuntimeError(f"RKNN 내보내기 실패 (ret={ret})")

    rknn.release()
    logger.info("변환 완료: %s", output_path)
    logger.info("Orange Pi 5로 전송 예시: scp %s raseyes:~/RasEyes/", output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="YOLOv8n → RKNN INT8 변환")
    parser.add_argument("--model", default="yolov8n.pt", help="YOLOv8 .pt 파일 경로")
    parser.add_argument("--output", default="yolov8n.rknn", help="출력 .rknn 파일 경로")
    parser.add_argument("--dataset", default="dataset.txt", help="양자화 보정 이미지 목록")
    parser.add_argument(
        "--no-quant",
        action="store_true",
        help="INT8 양자화 비활성화 (FP16 모드, 정확도 우선)",
    )
    args = parser.parse_args()

    onnx_path = args.model.replace(".pt", ".onnx")

    # Step 1: ONNX 내보내기
    if not os.path.exists(onnx_path):
        _export_onnx(args.model, onnx_path)
    else:
        logger.info("기존 ONNX 파일 재사용: %s", onnx_path)

    # Step 2: RKNN 변환
    _convert_rknn(
        onnx_path=onnx_path,
        output_path=args.output,
        dataset_path=args.dataset,
        do_quantization=not args.no_quant,
    )


if __name__ == "__main__":
    main()
