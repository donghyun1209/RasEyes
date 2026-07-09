"""YOLOv8 Nano 기반 객체 탐지기."""
import logging
from typing import List, Optional

import numpy as np

import config
from vision.interface import BaseCameraHAL, DetectionResult, VisionInterface

logger = logging.getLogger(__name__)


def _select_device(requested: Optional[str]) -> str:
    """추론 디바이스를 선택한다. MPS → CUDA → CPU 우선순위."""
    if requested is not None:
        return requested
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


class YoloDetector(VisionInterface):
    """YOLOv8 Nano 기반 실시간 객체 탐지기.

    BaseCameraHAL 인스턴스를 주입받아 프레임을 취득하고,
    YOLOv8 Nano 모델로 추론하여 DetectionResult 목록을 반환한다.

    Args:
        camera: 프레임 공급자. BaseCameraHAL 구현체.
        model_path: YOLO 가중치 경로 또는 모델 이름.
        conf_threshold: 탐지 결과 필터링 신뢰도 하한.
        device: 추론 장치 ("mps", "cpu", "cuda"). None이면 자동 선택.
    """

    def __init__(
        self,
        camera: BaseCameraHAL,
        model_path: str = config.YOLO_MODEL_PATH,
        conf_threshold: float = config.MIN_CONFIDENCE,
        device: Optional[str] = None,
    ) -> None:
        self._camera = camera
        self._model_path = model_path
        self._conf_threshold = conf_threshold
        self._device: str = _select_device(device)
        self._model = None
        self._started = False

    @property
    def conf_threshold(self) -> float:
        """현재 적용 중인 신뢰도 하한을 반환한다."""
        return self._conf_threshold

    def start(self) -> None:
        """카메라를 시작하고 YOLO 모델을 로드한다.

        이미 시작된 상태라면 기존 리소스를 해제 후 재초기화한다.

        Raises:
            RuntimeError: 모델 로드 실패 시.
            FileNotFoundError: 지정 경로에 모델 파일이 없을 때.
        """
        if self._started:
            self.stop()
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("ultralytics 패키지가 필요합니다: pip install ultralytics") from exc

        logger.info("YoloDetector 초기화 (model=%s, device=%s)", self._model_path, self._device)
        try:
            self._model = YOLO(self._model_path)
            self._model.to(self._device)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"모델 파일을 찾을 수 없습니다: {self._model_path}\n"
                "ultralytics가 자동 다운로드하려면 인터넷 연결이 필요합니다."
            )
        except Exception as exc:
            raise RuntimeError(f"모델 로드 실패: {exc}") from exc

        # Metal/CUDA JIT 컴파일 지연 제거를 위한 더미 추론
        warmup_frame = np.zeros(
            (config.FRAME_HEIGHT, config.FRAME_WIDTH, 3), dtype=np.uint8
        )
        self._model.predict(warmup_frame, conf=self._conf_threshold, device=self._device, verbose=False)
        logger.info("YoloDetector warmup 완료")

        self._camera.start()
        self._started = True

    def get_frame_detections(self) -> tuple[np.ndarray, List[DetectionResult]]:
        """프레임 캡처 및 추론을 실행하고 결과를 반환한다.

        Returns:
            (frame, detections) 튜플.
            frame: 원본 BGR ndarray.
            detections: conf_threshold 이상의 탐지 결과 목록.

        Raises:
            RuntimeError: start() 미호출 시.
        """
        if not self._started or self._model is None:
            raise RuntimeError("start()를 먼저 호출하세요.")

        frame = self._camera.read_frame()
        results = self._model.predict(
            frame,
            conf=self._conf_threshold,
            device=self._device,
            verbose=False,
        )

        detections: List[DetectionResult] = []
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return frame, detections

        for box in boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            label = self._model.names[int(box.cls[0])]
            confidence = float(box.conf[0])
            detections.append(
                DetectionResult(
                    label=label,
                    confidence=confidence,
                    bbox=(int(x1), int(y1), int(x2), int(y2)),
                )
            )

        return frame, detections

    def set_conf_threshold(self, value: float) -> None:
        """추론 신뢰도 하한을 런타임에 갱신한다.

        Args:
            value: 새 신뢰도 하한 (0.0 ~ 1.0).

        Raises:
            ValueError: value가 [0.0, 1.0] 범위를 벗어날 때.
        """
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"conf_threshold는 0.0~1.0 범위여야 합니다: {value}")
        self._conf_threshold = value
        logger.info("conf_threshold 갱신: %.2f", value)

    def stop(self) -> None:
        """카메라 리소스를 해제하고 모델 가중치를 메모리에서 제거한다."""
        if self._started:
            self._camera.stop()
            self._model = None
            self._started = False
            logger.info("YoloDetector 종료")

    @property
    def device(self) -> str:
        """실제 사용 중인 추론 장치 이름을 반환한다."""
        return self._device
