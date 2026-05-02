"""후방 호환 재내보내기 — sensor.hal 사용을 권장한다."""
from sensor.hal import BaseToFHAL as ToFSensorInterface

__all__ = ["ToFSensorInterface"]
