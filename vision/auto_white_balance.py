"""OV13855 자동 화이트밸런스(AWB) 제어 법칙.

드라이버에 `red_balance`/`blue_balance` 컨트롤이 없어(2026-07-29 Pi 실측) 센서에
게인을 되먹일 수 없다. 그래서 캡처된 프레임에 소프트웨어 채널 게인을 곱한다.

2026-08-04 야외 클립에서 **회색이어야 할 보도블록 패치**의 채널비가 R/G=0.56~0.65,
B/G=0.65~0.84로 측정됐다. 장면의 자연 초록이 아니라 채널 게인 미보정이며, 이미지
구조(건물·차량 윤곽)는 정상이라 디모자이크 문제도 아니다. 같은 클립에 그레이월드를
걸고 PC에서 YOLO를 돌리면 탐지가 2건 → 5건으로 늘었다.

`AutoExposure`와 같은 이유로 **순수 로직**이다 (CLAUDE.md §4) — `now`를 인자로 받고
하드웨어 I/O와 시계 호출을 포함하지 않으므로, PC에서 합성 프레임만으로 수렴과
과보정 방지를 검증할 수 있다.
"""
import logging
from typing import Optional, Tuple

import cv2
import numpy as np

import config

logger = logging.getLogger(__name__)


class AutoWhiteBalance:
    """그레이월드 가정으로 채널 게인을 추정해 프레임 색을 보정한다.

    게인 재계산은 `CSI_AWB_UPDATE_INTERVAL_SEC` 주기로만 하고, 적용(LUT)은 매 프레임
    수행한다. 게인이 바뀌지 않는 동안 룩업 테이블을 재사용하므로 정상 상태 비용은
    `cv2.LUT` 한 번뿐이다.
    """

    def __init__(self) -> None:
        # BGR 순서. 무보정 상태에서 출발해 첫 측광까지 원본을 그대로 통과시킨다.
        self._gains = np.ones(3, dtype=np.float32)
        self._lut: Optional[np.ndarray] = None
        self._last_update: float = float("-inf")
        self._clamped: bool = False

    @property
    def gains(self) -> Tuple[float, float, float]:
        """현재 적용 중인 (B, G, R) 채널 게인. 보정 전에는 (1.0, 1.0, 1.0)."""
        return float(self._gains[0]), float(self._gains[1]), float(self._gains[2])

    def update(self, now: float, frame: np.ndarray) -> np.ndarray:
        """게인을 필요 시 갱신하고 보정된 프레임을 반환한다.

        Args:
            now: 단조 증가 시각 (초). 호출자가 `time.monotonic()`으로 구해 넘긴다.
            frame: BGR 프레임, shape (H, W, 3), dtype uint8.

        Returns:
            채널 게인이 적용된 BGR 프레임. 게인이 항등(첫 측광 전)이면 입력 배열을
            그대로 반환한다.
        """
        if now - self._last_update >= config.CSI_AWB_UPDATE_INTERVAL_SEC:
            self._last_update = now
            measured = self._measure(frame)
            if measured is not None:
                self._blend(measured)
        if self._lut is None:
            return frame
        return cv2.LUT(frame, self._lut)

    def _measure(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """유효 밝기 대역의 픽셀만으로 채널 게인을 추정한다.

        클리핑된 픽셀은 세 채널이 모두 255라 게인 추정을 망치고, 암부는 노이즈가
        지배해 색 정보가 없다. 두 대역을 모두 제외한다.

        Args:
            frame: BGR 프레임.

        Returns:
            (B, G, R) 게인 배열. 유효 픽셀이 `CSI_AWB_MIN_VALID_RATIO`에 못 미치면
            None (이 주기의 갱신을 건너뛴다).
        """
        small = cv2.resize(
            frame,
            (config.CSI_AWB_METER_WIDTH, config.CSI_AWB_METER_HEIGHT),
            interpolation=cv2.INTER_NEAREST,
        )
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mask = (gray >= config.CSI_AWB_DARK_THRESH) & (gray <= config.CSI_AWB_BRIGHT_THRESH)
        if float(np.count_nonzero(mask)) / gray.size < config.CSI_AWB_MIN_VALID_RATIO:
            return None

        means = small[mask].astype(np.float32).mean(axis=0)
        means = np.maximum(means, 1.0)
        # 채널 평균을 전체 평균으로 맞춘다. 특정 채널을 1.0으로 고정하지 않는 이유는
        # 전체 밝기를 보존해 AE 루프와 간섭하지 않기 위함이다 (AE는 보정 전 프레임을
        # 측광하지만, 로깅되는 frame_luma와 탐지 입력은 보정 후 프레임이다).
        return means.mean() / means

    def _blend(self, measured: np.ndarray) -> None:
        """측정 게인을 EMA로 섞고 클램프한 뒤 룩업 테이블을 재생성한다.

        Args:
            measured: 이번 주기에 측정된 (B, G, R) 게인.
        """
        alpha = config.CSI_AWB_SMOOTHING
        blended = (1.0 - alpha) * self._gains + alpha * measured
        clamped = np.clip(blended, config.CSI_AWB_GAIN_MIN, config.CSI_AWB_GAIN_MAX)
        self._set_clamped(bool(np.any(clamped != blended)))
        self._gains = clamped.astype(np.float32)
        self._lut = self._build_lut(self._gains)

    def _build_lut(self, gains: np.ndarray) -> np.ndarray:
        """채널 게인으로 룩업 테이블(256×1×3)을 만든다.

        ⚠️ 값에 게인을 곱하고 그대로 클리핑하면, 게인이 큰 채널(예: R)이 다른
        채널보다 먼저 255에서 클리핑돼 원래 무채색이던 밝은 픽셀이 마젠타로
        치우친다 (2026-08-04 야외 실측 — 흰색이 마젠타로 뜨는 버그). 측광
        (_measure)은 이미 클리핑 픽셀을 제외하므로 문제는 여기, 적용부에 있다.

        CSI_AWB_HIGHLIGHT_ROLLOFF_START 미만은 풀 게인(그린 캐스트 보정은 대부분
        이 대역에서 일어나므로 기존 동작과 동일), 그 위로는 255까지 게인을 선형으로
        1.0까지 낮춰 모든 채널이 255에서 함께 만나게 한다.

        Args:
            gains: (B, G, R) 채널 게인.

        Returns:
            shape (256, 1, 3) uint8 LUT, cv2.LUT에 바로 쓸 수 있는 형태.
        """
        idx = np.arange(256, dtype=np.float32)
        start = float(config.CSI_AWB_HIGHLIGHT_ROLLOFF_START)
        rolloff = np.clip((255.0 - idx) / (255.0 - start), 0.0, 1.0)
        eased_gains = 1.0 + (gains[None, :] - 1.0) * rolloff[:, None]
        ramp = idx[:, None] * eased_gains
        return np.clip(ramp, 0, 255).astype(np.uint8).reshape(256, 1, 3)

    def _set_clamped(self, clamped: bool) -> None:
        """클램프 상태를 갱신하고 전이 시에만 로깅한다.

        그레이월드는 한 색이 장면을 지배하면 과보정하므로, 클램프가 계속 걸린다면
        가정이 깨진 환경이라는 신호다.

        Args:
            clamped: 이번 갱신에서 게인이 한계에 잘렸는지 여부.
        """
        if clamped == self._clamped:
            return
        self._clamped = clamped
        if clamped:
            logger.warning(
                "AWB 게인 한계 도달 (B=%.2f G=%.2f R=%.2f) — 한 색이 지배하는 장면일 수 있습니다",
                *self.gains,
            )
        else:
            logger.info("AWB 게인 한계 해제 (B=%.2f G=%.2f R=%.2f)", *self.gains)
