"""카메라 HAL PC 모킹 구현체."""
from __future__ import annotations

from pathlib import Path
from typing import List, Union

import numpy as np

import config
from vision.interface import BaseCameraHAL


class MockCamera(BaseCameraHAL):
    """테스트용 Mock 카메라 HAL.

    실제 카메라 없이 프레임을 반환한다. source 파라미터에 따라
    빈 프레임, 단일 이미지, 또는 이미지 시퀀스를 순환 재생한다.

    Args:
        source: None이면 검은 빈 프레임 반환. 이미지 파일 경로(str/Path) 또는
                복수 경로 목록(list)이면 해당 이미지를 순환 반환.
                이미지 로드에는 opencv-python이 필요하다.
        width: 빈 프레임 너비 (픽셀). source=None일 때만 적용.
        height: 빈 프레임 높이 (픽셀). source=None일 때만 적용.
    """

    def __init__(
        self,
        source: Union[None, str, Path, List[Union[str, Path]]] = None,
        width: int = config.FRAME_WIDTH,
        height: int = config.FRAME_HEIGHT,
    ) -> None:
        self._source = source
        self._width = width
        self._height = height
        self._frames: List[np.ndarray] = []
        self._index: int = 0
        self._running: bool = False

    def start(self) -> None:
        """카메라를 초기화하고 소스에서 프레임을 로드한다.

        Raises:
            ImportError: 파일 소스 사용 시 opencv-python이 없을 때.
            FileNotFoundError: 지정된 이미지 파일이 존재하지 않을 때.
        """
        self._frames = self._load_frames()
        self._index = 0
        self._running = True

    def read_frame(self) -> np.ndarray:
        """현재 프레임을 반환하고 다음 프레임 인덱스로 전진한다.

        Returns:
            BGR 포맷 numpy 배열, shape (H, W, 3).

        Raises:
            RuntimeError: start() 호출 전 접근 시.
        """
        if not self._running:
            raise RuntimeError("MockCamera not started. Call start() first.")
        frame = self._frames[self._index % len(self._frames)].copy()
        self._index += 1
        return frame

    def stop(self) -> None:
        """카메라를 정지하고 로드된 프레임을 해제한다."""
        self._running = False
        self._frames = []
        self._index = 0

    @property
    def frame_count(self) -> int:
        """로드된 프레임 수를 반환한다."""
        return len(self._frames)

    # ------------------------------------------------------------------
    def _load_frames(self) -> List[np.ndarray]:
        """source 설정에 맞춰 프레임 목록을 로드한다.

        Returns:
            로드된 BGR 프레임 목록. source가 None이면 요청 해상도의 빈 프레임 1장.

        Raises:
            ImportError: 파일 소스 사용 시 opencv-python이 없을 때.
            FileNotFoundError: 지정된 이미지 파일이 존재하지 않을 때.
        """
        if self._source is None:
            return [np.zeros((self._height, self._width, 3), dtype=np.uint8)]

        paths: List[Path] = (
            [Path(self._source)]
            if isinstance(self._source, (str, Path))
            else [Path(p) for p in self._source]
        )

        try:
            import cv2  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "이미지 파일 로드에 opencv-python이 필요합니다: pip install opencv-python"
            ) from exc

        frames: List[np.ndarray] = []
        for path in paths:
            img = cv2.imread(str(path))
            if img is None:
                raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {path}")
            if img.shape[1] != self._width or img.shape[0] != self._height:
                img = cv2.resize(
                    img, (self._width, self._height), interpolation=cv2.INTER_NEAREST
                )
            frames.append(img)
        return frames
