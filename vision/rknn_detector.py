"""rknnlite2 NPU 기반 객체 탐지기 (Orange Pi 5, RK3588S)."""
import logging
from typing import List, Optional

import cv2
import numpy as np

import config
from vision.hal import BaseCameraHAL
from vision.interface import DetectionResult, VisionInterface

logger = logging.getLogger(__name__)

# YOLOv8n RKNN 출력 텐서 레이아웃
_RKNN_INPUT_SIZE = 640          # 정사각형 입력
_RKNN_NUM_CLASSES = 80          # COCO 클래스 수
_NMS_IOU_THRESHOLD = 0.45


class RknnDetector(VisionInterface):
    """rknnlite2로 NPU 추론을 수행하는 객체 탐지기.

    YOLOv8n을 INT8 RKNN 포맷으로 변환한 모델을 사용한다.
    ImportError 또는 RuntimeError 발생 시 main.py factory에서
    YoloDetector(device="cpu") fallback으로 대체된다.

    Args:
        camera: 프레임 공급자. BaseCameraHAL 구현체.
        model_path: .rknn 모델 파일 경로.
        conf_threshold: 탐지 결과 필터링 신뢰도 하한.
    """

    def __init__(
        self,
        camera: BaseCameraHAL,
        model_path: str = config.RKNN_MODEL_PATH,
        conf_threshold: float = config.MIN_CONFIDENCE,
    ) -> None:
        self._camera = camera
        self._model_path = model_path
        self._conf_threshold = conf_threshold
        self._rknn = None
        self._started = False
        self._orig_w: int = config.FRAME_WIDTH
        self._orig_h: int = config.FRAME_HEIGHT

    @property
    def conf_threshold(self) -> float:
        return self._conf_threshold

    def start(self) -> None:
        """RKNN 런타임을 초기화하고 카메라를 시작한다.

        Raises:
            ImportError: rknnlite2 패키지 미설치 시.
            RuntimeError: 모델 로드 또는 런타임 초기화 실패 시.
        """
        if self._started:
            self.stop()

        try:
            from rknnlite.api import RKNNLite  # lazy import (Orange Pi 5 전용)
        except ImportError as exc:
            raise ImportError(
                "rknnlite2가 필요합니다 (Orange Pi 5 전용 패키지)"
            ) from exc

        self._rknn = RKNNLite()
        ret = self._rknn.load_rknn(self._model_path)
        if ret != 0:
            self._rknn = None
            raise RuntimeError(
                f"RKNN 모델 로드 실패 (ret={ret}): {self._model_path}"
            )

        ret = self._rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0_1)
        if ret != 0:
            self._rknn.release()
            self._rknn = None
            raise RuntimeError(f"RKNN 런타임 초기화 실패 (ret={ret})")

        self._camera.start()
        self._started = True
        logger.info("RknnDetector 시작 (model=%s)", self._model_path)

    def get_frame_detections(self) -> tuple[np.ndarray, List[DetectionResult]]:
        """프레임 캡처 및 NPU 추론을 실행하고 결과를 반환한다.

        Returns:
            (frame, detections) 튜플.
            frame: 원본 BGR ndarray.
            detections: conf_threshold 이상의 탐지 결과 목록.

        Raises:
            RuntimeError: start() 미호출 시.
        """
        if not self._started or self._rknn is None:
            raise RuntimeError("start()를 먼저 호출하세요.")

        frame = self._camera.read_frame()
        self._orig_h, self._orig_w = frame.shape[:2]

        # 전처리: BGR→RGB, 640×640 리사이즈, 배치 차원 추가
        img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (_RKNN_INPUT_SIZE, _RKNN_INPUT_SIZE))
        img = np.expand_dims(img, axis=0)  # (H,W,C) → (1,H,W,C)

        outputs = self._rknn.inference(inputs=[img])
        detections = self._postprocess(outputs)
        return frame, detections

    def _postprocess(self, outputs) -> List[DetectionResult]:
        """YOLOv8 RKNN 출력 텐서를 DetectionResult 목록으로 변환한다.

        출력 텐서 레이아웃: (1, 84, 8400)
        - 앞 4개 행: cx, cy, w, h (RKNN input 640×640 기준)
        - 나머지 80개 행: 클래스 확률

        Args:
            outputs: rknn.inference() 반환값.

        Returns:
            NMS 적용 후 conf_threshold 이상의 탐지 결과 목록.
        """
        if outputs is None or len(outputs) == 0:
            return []

        pred = outputs[0]  # shape: (1, 84, 8400)
        if pred is None or pred.ndim < 3:
            return []

        pred = pred[0].T  # (8400, 84)

        boxes_cxcywh = pred[:, :4]
        class_scores = pred[:, 4:]  # (8400, 80)
        class_ids = np.argmax(class_scores, axis=1)
        confidences = class_scores[np.arange(len(class_ids)), class_ids]

        mask = confidences >= self._conf_threshold
        if not np.any(mask):
            return []

        boxes_cxcywh = boxes_cxcywh[mask]
        confidences = confidences[mask]
        class_ids = class_ids[mask]

        # cx,cy,w,h → x1,y1,w,h (NMSBoxes 입력 형식)
        x1 = boxes_cxcywh[:, 0] - boxes_cxcywh[:, 2] / 2
        y1 = boxes_cxcywh[:, 1] - boxes_cxcywh[:, 3] / 2
        boxes_xywh = np.stack([x1, y1, boxes_cxcywh[:, 2], boxes_cxcywh[:, 3]], axis=1)

        indices = cv2.dnn.NMSBoxes(
            boxes_xywh.tolist(),
            confidences.tolist(),
            self._conf_threshold,
            _NMS_IOU_THRESHOLD,
        )
        if len(indices) == 0:
            return []

        # 좌표를 원본 해상도로 역변환
        scale_x = self._orig_w / _RKNN_INPUT_SIZE
        scale_y = self._orig_h / _RKNN_INPUT_SIZE

        detections: List[DetectionResult] = []
        for i in indices.flatten():
            cx, cy, w, h = boxes_cxcywh[i]
            x1_o = int((cx - w / 2) * scale_x)
            y1_o = int((cy - h / 2) * scale_y)
            x2_o = int((cx + w / 2) * scale_x)
            y2_o = int((cy + h / 2) * scale_y)
            detections.append(
                DetectionResult(
                    label=str(class_ids[i]),
                    confidence=float(confidences[i]),
                    bbox=(x1_o, y1_o, x2_o, y2_o),
                )
            )
        return detections

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
        """RKNN 런타임과 카메라 리소스를 해제한다."""
        if self._started:
            if self._rknn is not None:
                self._rknn.release()
                self._rknn = None
            self._camera.stop()
            self._started = False
            logger.info("RknnDetector 종료")
