"""센서 모듈 PC 모킹 구현체."""
from sensor.interface import ToFSensorInterface


class MockToFSensor(ToFSensorInterface):
    """테스트용 Mock ToF 센서.

    실제 VL53L1X 하드웨어 없이 지정된 거리값을 반환하므로 PC 환경에서 테스트 가능.

    Args:
        distance_cm: read_distance_cm 호출 시 반환할 초기 거리값 (cm).
    """

    def __init__(self, distance_cm: float = 200.0) -> None:
        self._distance_cm = distance_cm
        self._running = False

    def start(self) -> None:
        self._running = True

    def read_distance_cm(self) -> float:
        if not self._running:
            raise RuntimeError("ToF sensor not started. Call start() first.")
        return self._distance_cm

    def set_distance(self, distance_cm: float) -> None:
        """테스트 시 반환할 거리값을 변경한다."""
        self._distance_cm = distance_cm

    def stop(self) -> None:
        self._running = False
