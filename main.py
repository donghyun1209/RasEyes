"""RasEyes 메인 오케스트레이션.

각 도메인 모듈을 조립하고 메인 루프를 구동한다.
비즈니스 로직은 fusion.engine에, 로깅은 logs.logger에 위임하며
이 파일은 파이프라인 연결과 스레드 조정만 담당한다.

환경 변수:
    RASEYES_MOCK=1: MockVision 사용 (카메라·모델 불필요, 기본값: 0).
"""
import logging
import os
import queue
import random
import threading
import time
from typing import Callable, List, Optional, Tuple

import config
from audio.beep_controller import BeepController
from audio.mock import MockAudio
from fusion.engine import FusionEngine, RiskLevel
from logs.logger import CsvLogger
from sensor.hal import BaseToFHAL
from sensor.mock import MockToFSensor
from vision.detector import YoloDetector
from vision.interface import DetectionResult, VisionInterface
from vision.mock import MockVision
from vision.opencv_camera import OpenCVCamera

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 각 큐는 최신 1개만 유지하면 충분하지만 burst 여유를 위해 2슬롯 확보
_Q_SIZE = 2
_FPS_EMA_ALPHA: float = 0.2   # 실측 FPS EMA 평활화 계수


def _put_latest(q: "queue.Queue", item: object) -> None:
    """큐에 최신 항목만 유지한다. 가득 차면 기존 항목을 버리고 교체."""
    try:
        q.put_nowait(item)
    except queue.Full:
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        q.put_nowait(item)


def _vision_worker(
    vision: VisionInterface,
    stop_event: threading.Event,
    out_q: "queue.Queue[Tuple[float, object, List[DetectionResult]]]",
) -> None:
    """비전 캡처+탐지를 별도 스레드에서 실행하고 결과를 큐에 넣는다."""
    consecutive_failures = 0
    while not stop_event.is_set():
        try:
            frame, detections = vision.get_frame_detections()
            _put_latest(out_q, (time.monotonic(), frame, detections))
            consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            if consecutive_failures > config.REINIT_MAX_RETRIES:
                logger.critical(
                    "Vision 워커 최대 재시도(%d) 초과, 스레드 종료",
                    config.REINIT_MAX_RETRIES,
                )
                break
            logger.warning(
                "Vision 워커 오류 (%d/%d), 재초기화 시도: %s",
                consecutive_failures,
                config.REINIT_MAX_RETRIES,
                exc,
            )
            try:
                vision.start()
            except Exception as reinit_exc:
                logger.error("Vision 재초기화 실패: %s", reinit_exc)
                time.sleep(config.REINIT_DELAY_SEC)


def _sensor_worker(
    sensor: BaseToFHAL,
    stop_event: threading.Event,
    out_q: "queue.Queue[Tuple[float, float]]",
    on_reinit: Optional[Callable[[], None]] = None,
) -> None:
    """ToF 거리 읽기를 별도 스레드에서 실행하고 결과를 큐에 넣는다."""
    interval = 1.0 / config.TARGET_FPS
    consecutive_failures = 0
    while not stop_event.is_set():
        try:
            distance = sensor.read_distance_cm()
            _put_latest(out_q, (time.monotonic(), distance))
            consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            if consecutive_failures > config.REINIT_MAX_RETRIES:
                logger.critical(
                    "센서 워커 최대 재시도(%d) 초과, 스레드 종료",
                    config.REINIT_MAX_RETRIES,
                )
                break
            logger.warning(
                "센서 워커 오류 (%d/%d), 재초기화 시도: %s",
                consecutive_failures,
                config.REINIT_MAX_RETRIES,
                exc,
            )
            try:
                sensor.start()
                if on_reinit is not None:
                    on_reinit()
            except Exception as reinit_exc:
                logger.error("센서 재초기화 실패: %s", reinit_exc)
                time.sleep(config.REINIT_DELAY_SEC)
        time.sleep(interval)


def main() -> None:
    """RasEyes 메인 루프 (병렬 비전·센서 스레드)."""
    _use_mock = os.getenv("RASEYES_MOCK", "0") == "1"
    vision: VisionInterface = MockVision() if _use_mock else YoloDetector(camera=OpenCVCamera())
    sensor = MockToFSensor(distance_cm=200.0)
    fusion = FusionEngine()
    audio = MockAudio()
    beep = BeepController()
    csv_logger = CsvLogger()

    vision.start()
    sensor.start()
    audio.start()
    csv_logger.open()

    vision_q: queue.Queue = queue.Queue(maxsize=_Q_SIZE)
    sensor_q: queue.Queue = queue.Queue(maxsize=_Q_SIZE)
    stop_event = threading.Event()

    v_thread = threading.Thread(
        target=_vision_worker,
        args=(vision, stop_event, vision_q),
        daemon=True,
        name="vision-worker",
    )
    s_thread = threading.Thread(
        target=_sensor_worker,
        args=(sensor, stop_event, sensor_q, fusion.reset_filter),
        daemon=True,
        name="sensor-worker",
    )
    v_thread.start()
    s_thread.start()

    # 워커가 첫 결과를 올리기 전까지 사용할 안전한 초기값
    last_detections: List[DetectionResult] = []
    last_distance: float = float(config.MID_RISK_DIST_CM) + 1.0  # NONE 구간

    # 데이터 최신성 추적용 타임스탬프 (None = 아직 데이터 없음)
    last_vision_ts: Optional[float] = None
    last_sensor_ts: Optional[float] = None

    # 실측 FPS 상태 (메인 루프 + 비전 워커 별도 추적)
    actual_fps: float = float(config.TARGET_FPS)
    vision_fps: float = float(config.TARGET_FPS)
    prev_loop_start: float = time.monotonic()
    prev_vision_ts: Optional[float] = None
    fps_fallback_active: bool = False

    last_log_time = time.monotonic()
    frame_interval = 1.0 / config.TARGET_FPS

    mode = "Mock" if _use_mock else "YoloDetector"
    logger.info("RasEyes 시작 (%s 모드, 병렬 스레드)", mode)
    try:
        while True:
            loop_start = time.monotonic()

            # 실측 FPS 계산 (EMA 적용)
            iter_time = loop_start - prev_loop_start
            if iter_time > 0:
                actual_fps = (1 - _FPS_EMA_ALPHA) * actual_fps + _FPS_EMA_ALPHA * (1.0 / iter_time)
            prev_loop_start = loop_start

            # 비전 큐: 남은 프레임 예산만큼 블로킹 대기 (데이터 즉시 처리 보장)
            _vision_wait = max(0.0, frame_interval - (time.monotonic() - loop_start))
            try:
                vision_ts, _, last_detections = vision_q.get(timeout=_vision_wait)
                if prev_vision_ts is not None:
                    v_iter = vision_ts - prev_vision_ts
                    if v_iter > 0:
                        vision_fps = (
                            (1 - _FPS_EMA_ALPHA) * vision_fps
                            + _FPS_EMA_ALPHA / v_iter
                        )
                prev_vision_ts = vision_ts
                last_vision_ts = vision_ts
            except queue.Empty:
                pass
            try:
                sensor_ts, last_distance = sensor_q.get_nowait()
                last_sensor_ts = sensor_ts
            except queue.Empty:
                pass

            # 데이터 최신성 체크: 일정 시간 이상 경과한 데이터는 무효 처리
            now = time.monotonic()
            if (
                last_vision_ts is not None
                and now - last_vision_ts > config.DATA_STALENESS_THRESHOLD_SEC
            ):
                logger.warning(
                    "비전 데이터 만료 (%.2fs 경과), 탐지 결과 초기화",
                    now - last_vision_ts,
                )
                last_detections = []
            if (
                last_sensor_ts is not None
                and now - last_sensor_ts > config.DATA_STALENESS_THRESHOLD_SEC
            ):
                logger.warning(
                    "센서 데이터 만료 (%.2fs 경과), 안전 거리로 초기화",
                    now - last_sensor_ts,
                )
                last_distance = float(config.MID_RISK_DIST_CM) + 1.0

            # FPS 기준 미달 시 ToF 단독 모드 Fallback (루프·비전 워커 중 낮은 쪽 기준)
            effective_fps = min(actual_fps, vision_fps)
            if effective_fps < config.FPS_FALLBACK_THRESHOLD:
                if not fps_fallback_active:
                    logger.warning(
                        "FPS 기준 미달 (루프 %.1f FPS, 비전 %.1f FPS < %d), ToF 단독 모드 전환",
                        actual_fps,
                        vision_fps,
                        config.FPS_FALLBACK_THRESHOLD,
                    )
                    fps_fallback_active = True
                last_detections = []
            elif fps_fallback_active:
                logger.info(
                    "FPS 회복 (루프 %.1f FPS, 비전 %.1f FPS), 정상 모드 복귀",
                    actual_fps,
                    vision_fps,
                )
                fps_fallback_active = False

            result = fusion.evaluate(
                last_detections,
                last_distance,
                min_confidence=vision.conf_threshold,
            )

            if beep.should_beep(result.risk_level):
                audio.play_alert(result.risk_level)

            now = time.monotonic()
            if now - last_log_time >= config.LOG_INTERVAL_SEC:
                cpu_temp = round(40.0 + random.uniform(-5.0, 5.0), 1) if _use_mock else 0.0
                csv_logger.write_row(
                    tof_distance_cm=result.distance_cm,
                    alert_triggered=result.risk_level != RiskLevel.NONE,
                    fps=max(0, round(actual_fps)),
                    cpu_temp=cpu_temp,
                )
                last_log_time = now

            elapsed = time.monotonic() - loop_start
            time.sleep(max(0.0, frame_interval - elapsed))

    except KeyboardInterrupt:
        logger.info("종료 신호 수신 (Ctrl+C)")
    finally:
        stop_event.set()
        v_thread.join(timeout=2.0)
        s_thread.join(timeout=2.0)
        vision.stop()
        sensor.stop()
        audio.stop()
        csv_logger.close()
        logger.info("RasEyes 종료")


if __name__ == "__main__":
    main()
