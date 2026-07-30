"""OV13855 자동 노출(AE) 제어 법칙.

드라이버에 `auto_exposure` 컨트롤이 없고 rkaiq 3A 데몬도 미설치라(2026-07-29 Pi 실측)
`exposure`/`analogue_gain` 수동 컨트롤을 직접 되먹여 노출을 맞춘다.

하드웨어 I/O와 시계 호출을 포함하지 않는 **순수 로직**이다. `now`를 인자로 받으므로
(CLAUDE.md §4) PC에서 합성 프레임만으로 수렴·헌팅을 검증할 수 있고, v4l2 쓰기는
호출자(`CSICameraHAL`)가 담당한다.
"""
import logging
from typing import Optional, Tuple

import cv2
import numpy as np

import config

logger = logging.getLogger(__name__)


class AutoExposure:
    """프레임 밝기를 재서 다음 (exposure, analogue_gain)을 계산한다.

    Args:
        exposure: 현재 센서에 적용되어 있는 노출값 (시드).
        gain: 현재 센서에 적용되어 있는 아날로그 게인 (시드).
    """

    def __init__(
        self,
        exposure: int = config.CSI_SENSOR_EXPOSURE,
        gain: int = config.CSI_SENSOR_GAIN,
    ) -> None:
        self._exposure = int(exposure)
        self._gain = int(gain)
        self._last_update: float = float("-inf")
        self._last_apply: float = float("-inf")
        self._settled: bool = False
        self._at_rail: bool = False
        self._mean_luma: float = 0.0
        self._clip_hi: float = 0.0

    @property
    def settled(self) -> bool:
        """AE가 프레임을 급히 필요로 하지 않는 상태인지 여부.

        저전력 모드 진입 판단에 쓰인다. "이번 사이클에 값이 바뀌지 않았다"로 정의하면
        조도가 늘 변하는 야외에서 AE가 상시 조정 중이라 저전력이 영영 걸리지 않으므로,
        **프레임이 실제로 못 쓸 수준으로 어긋났을 때만** False다.

        레일(하드웨어 상·하한)에 걸린 경우도 True다 — 밤처럼 영원히 수렴하지 못하는
        상황에서 FPS를 계속 붙들면 배터리만 소모한다.
        """
        return self._settled

    @property
    def mean_luma(self) -> float:
        """마지막으로 측광한 평균 휘도 (0~255). 측광 전에는 0.0."""
        return self._mean_luma

    def update(self, now: float, frame: np.ndarray) -> Optional[Tuple[int, int]]:
        """프레임을 측광해 새 (exposure, analogue_gain)을 계산한다.

        Args:
            now: 단조 증가 시각 (초). 호출자가 `time.monotonic()`으로 구해 넘긴다.
            frame: BGR 프레임, shape (H, W, 3).

        Returns:
            센서에 써야 할 (exposure, analogue_gain) 튜플. 변경이 필요 없거나
            settle/스로틀 구간이면 None.
        """
        if now - self._last_apply < config.CSI_AE_SETTLE_SEC:
            return None
        if now - self._last_update < config.CSI_AE_UPDATE_INTERVAL_SEC:
            return None
        self._last_update = now

        self._mean_luma, self._clip_hi = self._meter(frame)
        ratio = self._ratio(self._mean_luma, self._clip_hi)

        result: Optional[Tuple[int, int]] = None
        at_rail = False
        if ratio is not None:
            exposure, gain = self._distribute(ratio)
            if exposure == self._exposure and gain == self._gain:
                # 보정이 필요한데 하드웨어 범위에 막혀 한 스텝도 움직이지 못했다.
                at_rail = True
            else:
                self._exposure, self._gain = exposure, gain
                self._last_apply = now
                result = (exposure, gain)

        self._set_rail(at_rail)
        self._settled = at_rail or not self._is_urgent()
        return result

    def _is_urgent(self) -> bool:
        """마지막 측광 기준으로 프레임이 못 쓸 수준으로 어긋났는지 판단한다.

        Returns:
            클리핑이 한도를 넘었거나 평균 휘도가 목표에서 크게 벗어났으면 True.
        """
        return (
            self._clip_hi > config.CSI_AE_CLIP_LIMIT
            or abs(self._mean_luma - config.CSI_AE_TARGET_LUMA) > config.CSI_AE_URGENT_LUMA_ERROR
        )

    def _meter(self, frame: np.ndarray) -> Tuple[float, float]:
        """프레임을 다운샘플링해 평균 휘도와 클리핑 픽셀 비율을 낸다.

        Args:
            frame: BGR 프레임.

        Returns:
            (평균 휘도 0~255, 클리핑 픽셀 비율 0.0~1.0).
        """
        small = cv2.resize(
            frame,
            (config.CSI_AE_METER_WIDTH, config.CSI_AE_METER_HEIGHT),
            interpolation=cv2.INTER_NEAREST,
        )
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mean_luma = float(cv2.mean(gray)[0])
        clip_hi = float(np.count_nonzero(gray >= config.CSI_AE_CLIP_THRESH)) / gray.size
        return mean_luma, clip_hi

    def _ratio(self, mean_luma: float, clip_hi: float) -> Optional[float]:
        """목표 대비 노출량 배율을 구한다.

        Args:
            mean_luma: 평균 휘도.
            clip_hi: 클리핑 픽셀 비율.

        Returns:
            적용할 배율. 데드밴드 안이면 None.
        """
        if clip_hi > config.CSI_AE_CLIP_LIMIT:
            # 평균이 255에서 포화해 과노출 배율을 과소평가하므로 고정 배율로 걷어낸다.
            return config.CSI_AE_CLIP_STEP
        if abs(mean_luma - config.CSI_AE_TARGET_LUMA) <= config.CSI_AE_TOLERANCE:
            return None
        ratio = config.CSI_AE_TARGET_LUMA / max(mean_luma, 1.0)
        return min(max(ratio, 1.0 / config.CSI_AE_MAX_STEP_DOWN), config.CSI_AE_MAX_STEP_UP)

    def _distribute(self, ratio: float) -> Tuple[int, int]:
        """목표 노출량을 exposure와 gain에 배분한다.

        게인은 노이즈원이므로 노출을 `CSI_AE_EXPOSURE_MAX`까지 먼저 쓰고, 모자랄 때만
        게인을 올린다. 반대로 줄일 때는 게인이 먼저 최소치로 내려간다.

        Args:
            ratio: 현재 노출량에 곱할 배율.

        Returns:
            하드웨어 범위로 클램프한 (exposure, analogue_gain).
        """
        gain_mult = self._gain / config.CSI_AE_GAIN_MIN
        desired = self._exposure * gain_mult * ratio

        exposure = int(round(min(max(desired, config.CSI_AE_EXPOSURE_MIN),
                                 config.CSI_AE_EXPOSURE_MAX)))
        gain = int(round(min(max(desired / exposure * config.CSI_AE_GAIN_MIN,
                                 config.CSI_AE_GAIN_MIN), config.CSI_AE_GAIN_MAX)))
        return exposure, gain

    def _set_rail(self, at_rail: bool) -> None:
        """레일 상태를 갱신하고 전이 시에만 로깅한다.

        Args:
            at_rail: 하드웨어 범위에 막혀 보정하지 못하는 상태인지 여부.
        """
        if at_rail == self._at_rail:
            return
        self._at_rail = at_rail
        if at_rail:
            logger.warning(
                "AE 한계 도달 — 평균 휘도 %.1f (목표 %.1f), exposure=%d gain=%d에서 더 보정할 수 없습니다",
                self._mean_luma,
                config.CSI_AE_TARGET_LUMA,
                self._exposure,
                self._gain,
            )
        else:
            logger.info("AE 한계 해제 (평균 휘도 %.1f)", self._mean_luma)
