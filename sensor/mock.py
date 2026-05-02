"""센서 모듈 PC 모킹 구현체."""
from __future__ import annotations

from typing import List, Union

from sensor.hal import BaseToFHAL


class MockToFSensor(BaseToFHAL):
    """테스트용 Mock ToF 센서.

    실제 VL53L1X 하드웨어 없이 지정된 거리값을 반환한다.
    고정값 또는 시나리오별 시퀀스를 순환 재생하여 접근·후퇴 등의
    동적 시나리오를 시뮬레이션할 수 있다.

    Args:
        distance_cm: 고정 거리값(float) 또는 순환 재생할 거리 시퀀스(list[float]).
                     기본값 200.0 cm.

    Examples:
        고정값::

            sensor = MockToFSensor(distance_cm=80.0)

        접근 시나리오 시퀀스::

            sensor = MockToFSensor(distance_cm=[200.0, 150.0, 100.0, 80.0])
    """

    def __init__(self, distance_cm: Union[float, List[float]] = 200.0) -> None:
        if isinstance(distance_cm, (int, float)):
            self._sequence: List[float] = [float(distance_cm)]
        else:
            if not distance_cm:
                raise ValueError("distance_cm 시퀀스는 비어 있을 수 없습니다.")
            self._sequence = [float(v) for v in distance_cm]
        self._index: int = 0
        self._running: bool = False

    def start(self) -> None:
        self._running = True

    def read_distance_cm(self) -> float:
        """현재 시퀀스 인덱스의 거리값을 반환하고 다음 인덱스로 전진한다.

        Returns:
            측정된 거리 (cm).

        Raises:
            RuntimeError: start() 호출 전 접근 시.
        """
        if not self._running:
            raise RuntimeError("ToF sensor not started. Call start() first.")
        value = self._sequence[self._index % len(self._sequence)]
        self._index += 1
        return value

    def set_distance(self, distance_cm: float) -> None:
        """고정 거리값으로 재설정하고 인덱스를 초기화한다."""
        self._sequence = [float(distance_cm)]
        self._index = 0

    def set_sequence(self, sequence: List[float]) -> None:
        """순환 재생할 거리 시퀀스를 교체하고 인덱스를 초기화한다.

        Args:
            sequence: 순환 재생할 거리값 목록 (cm). 비어 있으면 안 된다.

        Raises:
            ValueError: sequence가 빈 리스트일 때.
        """
        if not sequence:
            raise ValueError("sequence는 비어 있을 수 없습니다.")
        self._sequence = [float(v) for v in sequence]
        self._index = 0

    def stop(self) -> None:
        self._running = False
