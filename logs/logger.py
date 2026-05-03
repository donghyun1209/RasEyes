"""파이프라인 상태 CSV 로거."""
import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Optional

import config

_logger = logging.getLogger(__name__)


class CsvLogger:
    """루프 반복마다의 파이프라인 상태를 CSV 파일에 기록한다.

    Attributes:
        FIELDNAMES: CSV 컬럼명 목록 (순서 고정).
    """

    FIELDNAMES = ["timestamp", "cpu_temp", "fps", "tof_distance_cm", "alert_triggered"]

    def __init__(self, path: str = config.LOG_FILE_PATH) -> None:
        self._path = path
        self._file: Optional[IO] = None
        self._writer: Optional[csv.DictWriter] = None

    def open(self) -> None:
        """파일을 열고 헤더를 기록한다.

        Raises:
            RuntimeError: 이미 열려 있는 경우.
        """
        if self._file is not None:
            raise RuntimeError("CsvLogger가 이미 열려 있습니다.")
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.FIELDNAMES)
        self._writer.writeheader()
        self._file.flush()
        _logger.info("CSV 로거 시작: %s", self._path)

    def write_row(
        self,
        *,
        tof_distance_cm: float,
        alert_triggered: bool,
        fps: int,
        cpu_temp: float = 0.0,
    ) -> None:
        """현재 상태를 1행 기록한다.

        Args:
            tof_distance_cm: 이동평균 필터 적용 후 ToF 거리 (cm).
            alert_triggered: 이번 프레임에서 경보가 발생했는지 여부.
            fps: 현재 실측 FPS. 반드시 명시적으로 전달해야 한다.
            cpu_temp: CPU 온도 (°C). RPi 이전 단계에서는 0.0.

        Raises:
            RuntimeError: open() 호출 전인 경우.
        """
        if self._writer is None:
            raise RuntimeError("CsvLogger가 열려 있지 않습니다. open()을 먼저 호출하세요.")
        self._writer.writerow(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "cpu_temp": cpu_temp,
                "fps": fps,
                "tof_distance_cm": tof_distance_cm,
                "alert_triggered": alert_triggered,
            }
        )

    def close(self) -> None:
        """파일을 플러시하고 닫는다."""
        if self._file is not None:
            self._file.flush()
            self._file.close()
            self._file = None
            self._writer = None
            _logger.info("CSV 로거 종료.")
