"""OV13855 MIPI CSI 카메라 HAL 구현체 (Orange Pi 5)."""
import logging
import subprocess
import time

import cv2
import numpy as np

import config
from vision.auto_exposure import AutoExposure
from vision.auto_white_balance import AutoWhiteBalance
from vision.interface import BaseCameraHAL

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
        rotate_180: 프레임을 180도 회전할지 여부 (모듈이 뒤집혀 장착된 경우).
    """

    def __init__(
        self,
        device_path: str = config.CSI_DEVICE_PATH,
        width: int = config.FRAME_WIDTH,
        height: int = config.FRAME_HEIGHT,
        fps: int = config.TARGET_FPS,
        rotate_180: bool = config.CSI_ROTATE_180,
    ) -> None:
        self._device_path = device_path
        self._width = width
        self._height = height
        self._fps = fps
        self._rotate_180 = rotate_180
        self._cap: cv2.VideoCapture | None = None
        self._needs_resize: bool = False
        self._ae = AutoExposure() if config.CSI_AE_ENABLED else None
        self._awb = AutoWhiteBalance() if config.CSI_AWB_ENABLED else None

    @property
    def ae_settled(self) -> bool:
        """AE가 더 이상 노출을 조정하지 않는 상태인지 여부.

        AE 비활성이면 항상 True다. 조정 중에는 프레임이 자주 필요하므로 호출자가
        저전력 모드 진입을 미루는 판단에 쓴다.
        """
        return self._ae is None or self._ae.settled

    @property
    def exposure_gain(self) -> tuple[int, int] | None:
        """현재 센서 (exposure, analogue_gain). AE 비활성이면 None."""
        return None if self._ae is None else self._ae.exposure_gain

    def _set_exposure_gain(self, exposure: int, gain: int, timeout: float) -> None:
        """센서 subdev에 노출과 아날로그 게인을 한 번에 쓴다.

        두 컨트롤을 콤마로 묶어 subprocess 호출을 1회로 유지한다 (Pi 실측 중앙값
        4.5ms). 실패해도 경고만 남기고 진행한다 — 다음 주기에 다시 시도된다.

        Args:
            exposure: v4l2 `exposure` 값.
            gain: v4l2 `analogue_gain` 값.
            timeout: subprocess 타임아웃 (초).
        """
        cmd = [
            "v4l2-ctl", "-d", config.CSI_SENSOR_SUBDEV,
            f"--set-ctrl=exposure={exposure},analogue_gain={gain}",
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.warning("노출/게인 설정 실패 (exposure=%d, gain=%d): %s", exposure, gain, exc)

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
             f'"m02_b_ov13855 2-0036":0[fmt:{fmt}]'],
            ["media-ctl", "-d", "/dev/media1", "--set-v4l2",
             f'"rkcif-mipi-lvds1":0[fmt:{fmt}]'],
            ["media-ctl", "-d", "/dev/media1", "--set-v4l2",
             f'"rkisp-isp-subdev":0[fmt:{fmt} {crop}]'],
            ["media-ctl", "-d", "/dev/media1", "--set-v4l2",
             f'"rkisp-isp-subdev":2[fmt:{out_fmt} {crop}]'],
            ["v4l2-ctl", "-d", config.CSI_DEVICE_PATH,
             f"--set-fmt-video=width={self._width},height={self._height},pixelformat=UYVY"],
        ]
        for cmd in cmds:
            try:
                subprocess.run(cmd, capture_output=True, timeout=5, check=False)
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                logger.warning("ISP 파이프라인 설정 건너뜀: %s", exc)
        self._set_exposure_gain(config.CSI_SENSOR_EXPOSURE, config.CSI_SENSOR_GAIN, timeout=5)

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
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, config.CAMERA_BUFFER_SIZE)
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

        rotate_180이 True면 회전까지 마친 프레임을 반환하므로, 추론과 이벤트 클립이
        모두 정방향 영상을 보게 된다.

        AE가 활성이면 이 프레임을 측광해 필요할 때만 센서 노출을 갱신한다. 데드밴드
        안에서는 v4l2 호출이 아예 발생하지 않아 정상 상태 비용은 측광(~0.3ms)뿐이다.

        AWB는 AE **다음에** 적용한다. AE는 센서 노출을 되먹이는 폐루프이므로 센서가
        실제로 낸 값(보정 전)을 측광해야 하고, 추론과 이벤트 클립은 색이 교정된
        프레임을 봐야 하기 때문이다.

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
        if self._rotate_180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        now = time.monotonic()
        if self._ae is not None:
            new_ctrl = self._ae.update(now, frame)
            if new_ctrl is not None:
                self._set_exposure_gain(*new_ctrl, timeout=config.CSI_AE_CTRL_TIMEOUT_SEC)
        if self._awb is not None:
            frame = self._awb.update(now, frame)
        return frame

    def stop(self) -> None:
        """VideoCapture 리소스를 해제한다."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("CSICameraHAL 종료")
