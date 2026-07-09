"""OpenCV VideoCapture 기반 카메라 HAL 구현체."""
import logging

import cv2
import numpy as np

import config
from vision.interface import BaseCameraHAL

logger = logging.getLogger(__name__)


class OpenCVCamera(BaseCameraHAL):
    """OpenCV VideoCapture를 감싸는 실제 카메라 HAL 구현체.

    Args:
        device_index: cv2.VideoCapture에 전달할 장치 번호.
        width: 요청 캡처 너비 (픽셀).
        height: 요청 캡처 높이 (픽셀).
        fps: 요청 캡처 FPS.
    """

    def __init__(
        self,
        device_index: int = 0,
        width: int = config.FRAME_WIDTH,
        height: int = config.FRAME_HEIGHT,
        fps: int = config.TARGET_FPS,
    ) -> None:
        self._device_index = device_index
        self._width = width
        self._height = height
        self._fps = fps
        self._cap: cv2.VideoCapture | None = None
        self._needs_resize: bool = False  # start()에서 실제 해상도 확인 후 결정

    def start(self) -> None:
        """VideoCapture를 열고 해상도·FPS를 설정한다.

        Raises:
            RuntimeError: 카메라 장치를 열 수 없을 때.
        """
        self._cap = cv2.VideoCapture(self._device_index)
        if not self._cap.isOpened():
            self._cap = None
            raise RuntimeError(
                f"카메라 장치를 열 수 없습니다 (device_index={self._device_index})"
            )
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, config.CAMERA_BUFFER_SIZE)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap.set(cv2.CAP_PROP_FPS, self._fps)

        actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._needs_resize = actual_w != self._width or actual_h != self._height
        if self._needs_resize:
            logger.warning(
                "카메라가 요청 해상도(%dx%d)를 지원하지 않아 실제 해상도(%dx%d)로 동작합니다. "
                "read_frame()에서 소프트웨어 리사이징이 수행됩니다.",
                self._width,
                self._height,
                actual_w,
                actual_h,
            )

        logger.info(
            "OpenCVCamera 시작 (device=%d, %dx%d @ %d FPS)",
            self._device_index,
            self._width,
            self._height,
            self._fps,
        )

    def read_frame(self) -> np.ndarray:
        """최신 BGR 프레임을 반환한다.

        Returns:
            shape (H, W, 3) BGR ndarray.

        Raises:
            RuntimeError: start() 미호출 또는 프레임 읽기 실패 시.
        """
        if self._cap is None:
            raise RuntimeError("start()를 먼저 호출하세요.")
        ret, frame = self._cap.read()
        if not ret or frame is None:
            raise RuntimeError("프레임 캡처 실패 — 카메라 연결을 확인하세요.")
        if self._needs_resize:
            frame = cv2.resize(frame, (self._width, self._height), interpolation=cv2.INTER_NEAREST)
        return frame

    def stop(self) -> None:
        """VideoCapture 리소스를 해제한다."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("OpenCVCamera 종료")
