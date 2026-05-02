"""센서 노이즈 제거 필터 모음."""
from collections import deque

import config


class MovingAverageFilter:
    """단순 이동 평균 필터.

    ToF 센서 원시값의 노이즈를 제거하기 위해 사용한다.
    window 크기만큼 값이 쌓이기 전까지는 현재 누적된 값들의 평균을 반환한다.

    Args:
        window: 이동 평균 윈도우 크기. 기본값은 config.MOVING_AVG_WINDOW.
    """

    def __init__(self, window: int = config.MOVING_AVG_WINDOW) -> None:
        if window < 1:
            raise ValueError(f"window는 1 이상이어야 합니다. (입력값: {window})")
        self._buf: deque[float] = deque(maxlen=window)

    def update(self, value: float) -> float:
        """새 값을 추가하고 현재 이동 평균을 반환한다.

        Args:
            value: 새 ToF 원시 측정값 (cm).

        Returns:
            현재 버퍼에 쌓인 값들의 산술 평균 (cm).
        """
        self._buf.append(value)
        return sum(self._buf) / len(self._buf)

    def reset(self) -> None:
        """버퍼를 초기화한다."""
        self._buf.clear()

    @property
    def window(self) -> int:
        """설정된 윈도우 크기를 반환한다."""
        return self._buf.maxlen  # type: ignore[return-value]
