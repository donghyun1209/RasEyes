"""RasEyes 메인 오케스트레이션.

각 도메인 모듈을 조립하고 메인 루프를 구동한다.
비즈니스 로직은 fusion.engine에 위임하며 이 파일은 파이프라인 연결만 담당한다.
"""
import csv
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import config
from audio.mock import MockAudio
from fusion.engine import FusionEngine, RiskLevel
from sensor.mock import MockToFSensor
from vision.mock import MockVision

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _open_log_writer(path: str) -> tuple:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(
        f,
        fieldnames=["timestamp", "cpu_temp", "fps", "tof_distance_cm", "alert_triggered"],
    )
    writer.writeheader()
    return f, writer


def main() -> None:
    """RasEyes 메인 루프."""
    vision = MockVision()
    sensor = MockToFSensor(distance_cm=200.0)
    fusion = FusionEngine()
    audio = MockAudio()

    vision.start()
    sensor.start()
    audio.start()

    log_file, log_writer = _open_log_writer(config.LOG_FILE_PATH)
    last_log_time = time.monotonic()
    frame_interval = 1.0 / config.TARGET_FPS

    logger.info("RasEyes 시작 (Mock 모드)")
    try:
        while True:
            loop_start = time.monotonic()

            _, detections = vision.get_frame_detections()
            distance = sensor.read_distance_cm()
            result = fusion.evaluate(detections, distance)

            if result.risk_level != RiskLevel.NONE:
                audio.play_alert(result.risk_level)

            now = time.monotonic()
            if now - last_log_time >= config.LOG_INTERVAL_SEC:
                log_writer.writerow({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "cpu_temp": 0.0,
                    "fps": config.TARGET_FPS,
                    "tof_distance_cm": distance,
                    "alert_triggered": result.risk_level != RiskLevel.NONE,
                })
                last_log_time = now

            elapsed = time.monotonic() - loop_start
            time.sleep(max(0.0, frame_interval - elapsed))

    except KeyboardInterrupt:
        logger.info("종료 신호 수신 (Ctrl+C)")
    finally:
        vision.stop()
        sensor.stop()
        audio.stop()
        log_file.close()
        logger.info("RasEyes 종료")


if __name__ == "__main__":
    main()
