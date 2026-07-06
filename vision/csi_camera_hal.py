"""OV13855 MIPI CSI 카메라 HAL 구현체 (Orange Pi 5)."""
import logging
import subprocess

import cv2
import numpy as np

import config
from vision.hal import BaseCameraHAL

logger = logging.getLogger(__name__)


class CSICameraHAL(BaseCameraHAL):
    """OpenCV VideoCapture로 MIPI CSI 카메라를 제어하는 HAL 구현체.

    Orange Pi 5의 OV13855 카메라는 `/dev/video11` (rkisp mainpath)로
    접근한다. 장치 경로 문자열을 직접 받는다는 점이 OpenCVCamera와 다르다.

    Args:
        device_path: VideoCapture에 전달할 장치 파일 경로.
        width: 요청 캡처 너비 (픽셀).
        height: 요청 캡처 높이 (픽셀).
        fps: 요청 캡처 FPS.
    """

    def __init__(
        self,
        device_path: str = config.CSI_DEVICE_PATH,
        width: int = config.FRAME_WIDTH,
        height: int = config.FRAME_HEIGHT,
        fps: int = config.TARGET_FPS,
    ) -> None:
        self._device_path = device_path
        self._width = width
        self._height = height
        self._fps = fps
        self._cap: cv2.VideoCapture | None = None
        self._needs_resize: bool = False

    def _setup_isp_pipeline(self) -> None:
        """ISP 미디어 파이프라인과 센서 노출을 초기화한다.

        재부팅 후 rkisp ISP pad2(출력)이 미설정 상태로 남아 검은 프레임을 출력하는
        현상을 방지한다. 실패해도 경고만 남기고 진행한다.
        """
        fmt = f"SBGGR10_1X10/{self._width}x{self._height}"
        out_fmt = f"YUYV8_2X8/{self._width}x{self._height}"
        crop = f"crop:(0,0)/{self._width}x{self._height}"
        cmds = [
            ["media-ctl", "-d", "/dev/media0", "--set-v4l2",
             f'"m01_b_ov13855 7-0036":0[fmt:{fmt}]'],
            ["media-ctl", "-d", "/dev/media1", "--set-v4l2",
             f'"rkcif-mipi-lvds":0[fmt:{fmt}]'],
            ["media-ctl", "-d", "/dev/media1", "--set-v4l2",
             f'"rkisp-isp-subdev":0[fmt:{fmt} {crop}]'],
            ["media-ctl", "-d", "/dev/media1", "--set-v4l2",
             f'"rkisp-isp-subdev":2[fmt:{out_fmt} {crop}]'],
            ["v4l2-ctl", "-d", config.CSI_DEVICE_PATH,
             f"--set-fmt-video=width={self._width},height={self._height},pixelformat=UYVY"],
            ["v4l2-ctl", "-d", config.CSI_SENSOR_SUBDEV,
             f"--set-ctrl=exposure={config.CSI_SENSOR_EXPOSURE},"
             f"analogue_gain={config.CSI_SENSOR_GAIN}"],
        ]
        for cmd in cmds:
            try:
                subprocess.run(cmd, capture_output=True, timeout=5, check=False)
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                logger.warning("ISP 파이프라인 설정 건너뜀: %s", exc)

    def start(self) -> None:
        """VideoCapture를 열고 해상도·FPS를 설정한다.

        Raises:
            RuntimeError: 카메라 장치를 열 수 없을 때.
        """
        self._setup_isp_pipeline()
        self._cap = cv2.VideoCapture(self._device_path)
        if not self._cap.isOpened():
            self._cap = None
            raise RuntimeError(
                f"CSI 카메라를 열 수 없습니다 (device_path={self._device_path})"
            )
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap.set(cv2.CAP_PROP_FPS, self._fps)

        actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._needs_resize = actual_w != self._width or actual_h != self._height
        if self._needs_resize:
            logger.warning(
                "CSI 카메라가 요청 해상도(%dx%d)를 지원하지 않아 실제 해상도(%dx%d)로 동작합니다. "
                "read_frame()에서 소프트웨어 리사이징이 수행됩니다.",
                self._width,
                self._height,
                actual_w,
                actual_h,
            )

        logger.info(
            "CSICameraHAL 시작 (device=%s, %dx%d @ %d FPS)",
            self._device_path,
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
            raise RuntimeError("CSI 카메라 프레임 캡처 실패 — 카메라 연결을 확인하세요.")
        if self._needs_resize:
            frame = cv2.resize(
                frame, (self._width, self._height), interpolation=cv2.INTER_NEAREST
            )
        return frame

    def stop(self) -> None:
        """VideoCapture 리소스를 해제한다."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("CSICameraHAL 종료")
