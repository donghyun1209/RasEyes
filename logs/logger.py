"""CSV 기반 RasEyes 운영 로거."""
import csv
import datetime
import logging
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)


class CsvLogger:
    """1초 1회 운영 데이터를 CSV 파일에 기록하는 로거.

    Args:
        path: CSV 파일 저장 경로.
    """

    FIELDNAMES = ["timestamp", "cpu_temp", "fps", "tof_distance_cm", "alert_triggered", "latency_ms"]

    def __init__(self, path: str = config.LOG_FILE_PATH) -> None:
        self._path = path
        self._file = None
        self._writer: Optional[csv.DictWriter] = None

    def open(self) -> None:
        """CSV 파일을 열고 헤더를 작성한다. 중간 디렉터리가 없으면 자동 생성한다.

        Raises:
            RuntimeError: 이미 열려 있을 때.
        """
        if self._file is not None:
            raise RuntimeError("CsvLogger가 이미 열려 있습니다. close() 후 재호출하세요.")
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.FIELDNAMES)
        self._writer.writeheader()
        logger.info("CsvLogger 시작: %s", self._path)

    def write_row(
        self,
        tof_distance_cm: float,
        alert_triggered: bool,
        fps: int,
        cpu_temp: float = 0.0,
        latency_ms: float = 0.0,
    ) -> None:
        """현재 운영 데이터를 한 행으로 기록한다.

        Args:
            tof_distance_cm: 퓨전 엔진이 반환한 필터링된 ToF 거리 (cm).
            alert_triggered: 이 사이클에서 경보가 발생했는지 여부.
            fps: 현재 실측 FPS (반올림 정수).
            cpu_temp: CPU 온도 (°C). 기본값 0.0 (측정 불가 환경).
            latency_ms: E2E 레이턴시 EMA (ms). 기본값 0.0.

        Raises:
            RuntimeError: open() 미호출 시.
        """
        if self._writer is None:
            raise RuntimeError("open()을 먼저 호출하세요.")
        self._writer.writerow(
            {
                "timestamp": datetime.datetime.now().isoformat(timespec="milliseconds"),
                "cpu_temp": cpu_temp,
                "fps": fps,
                "tof_distance_cm": round(tof_distance_cm, 2),
                "alert_triggered": alert_triggered,
                "latency_ms": round(latency_ms, 1),
            }
        )
        self._file.flush()

    def close(self) -> None:
        """CSV 파일을 닫는다."""
        if self._file is not None:
            self._file.close()
            self._file = None
            self._writer = None
            logger.info("CsvLogger 종료: %s", self._path)
