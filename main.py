"""RasEyes 메인 오케스트레이션.

각 도메인 모듈을 조립하고 메인 루프를 구동한다.
비즈니스 로직은 fusion.engine에, 로깅은 logs.logger에 위임하며
이 파일은 파이프라인 연결과 스레드 조정만 담당한다.

환경 변수:
    RASEYES_MOCK=1: 모든 컴포넌트를 Mock으로 교체 (카메라·모델 불필요, 기본값: 0).
    RASEYES_HW=1:   하드웨어 HAL 사용 시도 (Orange Pi 5). 초기화 실패 시 자동 fallback.
"""
import logging
import os
import queue
import random
import signal
import subprocess
import threading
import time
from typing import Callable, List, Optional, Tuple

import numpy as np

import config
from audio.beep_controller import BeepController
from audio.boot_sequence import BootSequence
from audio.hal import BaseAudioHAL
from audio.mock import MockAudio
from audio.mock_tts import MockTts
from audio.tts import EspeakTts
from audio.tts_hal import BaseTtsHAL
from fusion.engine import FusionEngine, FusionResult, RiskLevel
from logs.logger import CsvLogger
from sensor.button_handler import ButtonHandler
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


def _read_cpu_temp() -> float:
    """Orange Pi 5의 CPU 온도를 섭씨로 반환한다. 읽기 실패 시 0.0 반환."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except OSError:
        return 0.0


def _read_battery_percent() -> Optional[int]:
    """배터리 잔량(%)을 반환한다. sysfs 경로가 없으면 None 반환."""
    try:
        with open(config.BATTERY_SYSFS_PATH) as f:
            return int(f.read().strip())
    except OSError:
        return None


def _build_vision(use_mock: bool, use_hw: bool) -> VisionInterface:
    """환경에 맞는 비전 컴포넌트를 생성한다."""
    if use_mock:
        return MockVision()
    if use_hw:
        try:
            from rknnlite.api import RKNNLite  # noqa: F401 — package availability check
            from vision.rknn_detector import RknnDetector
            from vision.csi_camera_hal import CSICameraHAL
            if not os.path.exists(config.RKNN_MODEL_PATH):
                raise RuntimeError(f"RKNN 모델 파일 없음: {config.RKNN_MODEL_PATH} — scp yolov8n.rknn raseyes:~/RasEyes/")
            return RknnDetector(camera=CSICameraHAL())
        except (ImportError, RuntimeError) as exc:
            logger.warning("RKNN 초기화 실패, YoloDetector(cpu) fallback: %s", exc)
        try:
            import ultralytics  # noqa: F401 — package availability check
            from vision.csi_camera_hal import CSICameraHAL
            return YoloDetector(camera=CSICameraHAL(), device="cpu")
        except Exception as exc:
            logger.warning("CSICameraHAL/YoloDetector 초기화 실패, OpenCVCamera fallback: %s", exc)
    try:
        import ultralytics  # noqa: F401 — package availability check
        return YoloDetector(camera=OpenCVCamera())
    except ImportError as exc:
        logger.warning("ultralytics 미설치, MockVision fallback (ToF+오디오는 정상 동작): %s", exc)
        return MockVision()


def _build_sensor(use_mock: bool, use_hw: bool) -> BaseToFHAL:
    """환경에 맞는 ToF 센서 컴포넌트를 생성한다."""
    if use_mock or not use_hw:
        return MockToFSensor(distance_cm=200.0)
    try:
        from sensor.vl53l1x_hal import VL53L1XHAL
        return VL53L1XHAL()
    except (ImportError, RuntimeError) as exc:
        logger.warning("VL53L1XHAL 초기화 실패, MockToFSensor fallback: %s", exc)
        return MockToFSensor(distance_cm=200.0)


def _build_audio(use_mock: bool, use_hw: bool) -> BaseAudioHAL:
    """환경에 맞는 오디오 컴포넌트를 생성한다."""
    if use_mock or not use_hw:
        return MockAudio()
    try:
        import sounddevice  # noqa: F401 — OSError if PortAudio lib missing
        from audio.jack_hal import JackAudioHAL
        return JackAudioHAL()
    except (ImportError, RuntimeError, OSError) as exc:
        logger.warning("JackAudioHAL 초기화 실패, MockAudio fallback: %s", exc)
        return MockAudio()


def _build_tts(use_mock: bool) -> BaseTtsHAL:
    """환경에 맞는 TTS 컴포넌트를 생성한다."""
    if use_mock:
        return MockTts()
    try:
        subprocess.run(
            ["espeak-ng", "--version"],
            capture_output=True,
            check=True,
            timeout=3,
        )
        # JackAudioHAL과 동일한 ES8388 장치 인덱스 공유 (장치 충돌 방지)
        device_idx = None
        try:
            import sounddevice as _sd
            device_idx = next(
                (i for i, d in enumerate(_sd.query_devices())
                 if "es8388" in d["name"].lower() and d["max_output_channels"] > 0),
                None,
            )
        except Exception:
            pass
        return EspeakTts(device_idx=device_idx)
    except (FileNotFoundError, subprocess.CalledProcessError, OSError) as exc:
        logger.warning("espeak-ng 없음, MockTts fallback: %s", exc)
        return MockTts()


def _build_tts_text(result: FusionResult) -> Optional[str]:
    """FusionResult에서 TTS 발화 텍스트를 생성한다.

    Returns:
        발화할 문자열. NONE 위험 수준이면 None.
    """
    if result.risk_level == RiskLevel.NONE:
        return None
    if result.tof_only_mode:
        if result.risk_level == RiskLevel.HIGH:
            return "위험, 전방 장애물"
        return "주의, 장애물"
    label = config.COCO_KO_LABELS.get(result.top_label or "", result.top_label or "물체")
    direction = result.direction or "정면"
    if result.risk_level == RiskLevel.HIGH:
        dist = round(result.distance_cm)
        return f"위험, {direction} {dist}센티 {label}"
    return f"{direction}에 {label}"


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
    heartbeat: List[float],
    throttle_event: Optional[threading.Event] = None,
) -> None:
    """비전 캡처+탐지를 별도 스레드에서 실행하고 결과를 큐에 넣는다.

    Args:
        vision: 비전 HAL 인터페이스.
        stop_event: 종료 신호 이벤트.
        out_q: (timestamp, frame, detections) 튜플을 담는 출력 큐.
        heartbeat: heartbeat[0]을 매 이터레이션마다 현재 시각으로 갱신한다.
                   메인 루프의 Watchdog이 이 값을 확인하여 스레드 스톨을 감지한다.
        throttle_event: 세트되면 추론 후 추가 슬립으로 FPS를 낮춘다 (발열 제어용).
    """
    consecutive_failures = 0
    while not stop_event.is_set():
        heartbeat[0] = time.monotonic()  # Watchdog 갱신: 추론 블로킹 전 기록
        try:
            frame, detections = vision.get_frame_detections()
            _put_latest(out_q, (time.monotonic(), frame, detections))
            consecutive_failures = 0
            if throttle_event is not None and throttle_event.is_set():
                time.sleep(1.0 / config.THERMAL_THROTTLE_FPS)
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
    """ToF 거리 읽기를 별도 스레드에서 실행하고 결과를 큐에 넣는다.

    Args:
        sensor: ToF HAL 인터페이스.
        stop_event: 종료 신호 이벤트.
        out_q: (timestamp, distance_cm) 튜플을 담는 출력 큐.
        on_reinit: 센서 재초기화 성공 시 호출할 콜백 (예: 필터 리셋).
    """
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


class RasEyesApp:
    """RasEyes 애플리케이션 오케스트레이터.

    컴포넌트 초기화, 워커 스레드 수명 주기, 메인 루프를 단일 클래스로
    캡슐화하여 가독성과 테스트 가능성을 높인다.
    """

    def __init__(self, use_mock: bool = False, use_hw: bool = False) -> None:
        """Args:
            use_mock: True이면 모든 컴포넌트를 Mock으로 교체.
            use_hw: True이면 Orange Pi 5 하드웨어 HAL 사용 시도.
        """
        self._use_mock = use_mock
        self._use_hw = use_hw
        self._vision: VisionInterface = _build_vision(use_mock, use_hw)
        self._sensor: BaseToFHAL = _build_sensor(use_mock, use_hw)
        self._fusion = FusionEngine()
        self._audio: BaseAudioHAL = _build_audio(use_mock, use_hw)
        self._tts: BaseTtsHAL = _build_tts(use_mock)
        self._beep = BeepController()
        self._csv_logger = CsvLogger()

        self._vision_q: queue.Queue = queue.Queue(maxsize=_Q_SIZE)
        self._sensor_q: queue.Queue = queue.Queue(maxsize=_Q_SIZE)
        self._stop_event = threading.Event()
        self._thermal_event = threading.Event()
        self._vision_heartbeat: List[float] = [time.monotonic()]

        self._v_thread: Optional[threading.Thread] = None
        self._s_thread: Optional[threading.Thread] = None

        self._button_handler: Optional[ButtonHandler] = None
        self._mute_active: bool = False

    def start(self) -> None:
        """모든 컴포넌트와 워커 스레드를 시작한다."""
        self._vision.start()
        self._sensor.start()
        self._audio.start()
        self._csv_logger.open()

        self._vision_heartbeat[0] = time.monotonic()
        self._v_thread = threading.Thread(
            target=_vision_worker,
            args=(self._vision, self._stop_event, self._vision_q, self._vision_heartbeat, self._thermal_event),
            daemon=True,
            name="vision-worker",
        )
        self._s_thread = threading.Thread(
            target=_sensor_worker,
            args=(self._sensor, self._stop_event, self._sensor_q, self._fusion.reset_filter),
            daemon=True,
            name="sensor-worker",
        )
        self._v_thread.start()
        self._s_thread.start()

        if self._use_hw:
            try:
                self._button_handler = ButtonHandler()
                self._button_handler.start(self._toggle_mute)
            except RuntimeError as exc:
                logger.warning("ButtonHandler 초기화 실패 (gpiod 미설치?): %s", exc)
                self._button_handler = None

        mode = "Mock" if self._use_mock else "YoloDetector"
        logger.info("RasEyes 시작 (%s 모드, 병렬 스레드)", mode)
        BootSequence().play(self._audio, self._tts)

    def _toggle_mute(self) -> None:
        """물리 버튼 누름 시 오디오 음소거 온/오프 전환."""
        self._mute_active = not self._mute_active
        logger.info("버튼 누름: 오디오 %s", "음소거" if self._mute_active else "음소거 해제")

    def stop(self) -> None:
        """모든 워커 스레드를 중단하고 컴포넌트를 정리한다."""
        self._stop_event.set()
        if self._button_handler is not None:
            self._button_handler.stop()
        if self._v_thread:
            self._v_thread.join(timeout=2.0)
        if self._s_thread:
            self._s_thread.join(timeout=2.0)
        self._vision.stop()
        self._sensor.stop()
        self._audio.stop()
        self._tts.stop()
        self._csv_logger.close()
        logger.info("RasEyes 종료")

    def _check_vision_stall(self) -> bool:
        """비전 워커가 VISION_STALL_THRESHOLD_SEC 이상 응답하지 않으면 경고를 기록하고 True를 반환한다."""
        elapsed = time.monotonic() - self._vision_heartbeat[0]
        if elapsed > config.VISION_STALL_THRESHOLD_SEC:
            logger.warning("비전 워커 응답 없음 (%.2fs 경과, 임계값 %.1fs)", elapsed, config.VISION_STALL_THRESHOLD_SEC)
            return True
        return False

    def run(self) -> None:
        """start() → 메인 루프 → stop() 전체 수명 주기를 실행한다."""
        def _on_sigterm(signum, frame):
            logger.info("종료 신호 수신 (SIGTERM)")
            self._stop_event.set()

        signal.signal(signal.SIGTERM, _on_sigterm)

        self.start()
        try:
            self._run_loop()
        except KeyboardInterrupt:
            logger.info("종료 신호 수신 (Ctrl+C)")
        finally:
            self.stop()

    def _run_loop(self) -> None:
        """메인 처리 루프: 비전/센서 큐 소비, Watchdog, 퓨전, 오디오, 로깅."""
        last_detections: List[DetectionResult] = []
        last_distance: float = float(config.MID_RISK_DIST_CM) + 1.0

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

        # 5-1: E2E 레이턴시 추적
        e2e_ms_ema: float = 0.0
        current_vision_ts: Optional[float] = None

        # 5-2: 발열 스로틀링 상태
        thermal_throttle_active: bool = False

        # 5-3: 카메라 가림 감지
        _prev_frame: Optional[object] = None
        _occlusion_counter: int = 0
        _last_occlusion_alert_time: float = 0.0

        # 5-4: 배터리 잔량 확인
        last_battery_check_time: float = 0.0

        # 7: TTS 로그 주기 내 마지막 발화 텍스트 추적
        _last_tts_text: str = ""

        while not self._stop_event.is_set():
            loop_start = time.monotonic()

            # 실측 FPS 계산 (EMA 적용)
            iter_time = loop_start - prev_loop_start
            if iter_time > 0:
                actual_fps = (1 - _FPS_EMA_ALPHA) * actual_fps + _FPS_EMA_ALPHA / iter_time
            prev_loop_start = loop_start

            # 비전 큐: 남은 프레임 예산만큼 블로킹 대기
            _vision_wait = max(0.0, frame_interval - (time.monotonic() - loop_start))
            try:
                vision_ts, last_frame, last_detections = self._vision_q.get(timeout=_vision_wait)
                current_vision_ts = vision_ts
                if prev_vision_ts is not None:
                    v_iter = vision_ts - prev_vision_ts
                    if v_iter > 0:
                        vision_fps = (
                            (1 - _FPS_EMA_ALPHA) * vision_fps
                            + _FPS_EMA_ALPHA / v_iter
                        )
                prev_vision_ts = vision_ts
                last_vision_ts = vision_ts

                # 5-3: 카메라 가림 감지 — 프레임 간 픽셀 변화량 분석
                if (
                    not self._use_mock
                    and last_frame is not None
                    and _prev_frame is not None
                ):
                    delta = float(
                        np.mean(np.abs(last_frame.astype(float) - _prev_frame.astype(float)))
                    )
                    if delta < config.CAMERA_OCCLUSION_CHANGE_THRESH:
                        _occlusion_counter += 1
                    else:
                        _occlusion_counter = 0
                _prev_frame = last_frame
            except queue.Empty:
                pass
            try:
                sensor_ts, last_distance = self._sensor_q.get_nowait()
                last_sensor_ts = sensor_ts
            except queue.Empty:
                pass

            # 데이터 최신성 체크
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

            # 비전 워커 Watchdog 체크
            self._check_vision_stall()

            # FPS 기준 미달 시 ToF 단독 모드 Fallback
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

            result = self._fusion.evaluate(
                last_detections,
                last_distance,
                min_confidence=self._vision.conf_threshold,
            )

            # 시스템 경고(배터리 등)와 퓨전 결과를 병합해 단일 오디오 채널로 직렬화
            pending_system = self._beep.pop_system_alert()
            effective_risk = (
                pending_system
                if pending_system is not None and pending_system.value > result.risk_level.value
                else result.risk_level
            )
            if self._beep.should_beep(effective_risk) and not self._mute_active:
                self._audio.play_alert(effective_risk)

            # TTS: 탐지 결과 기반 음성 알림 (비프음과 독립적으로 논블로킹 동작)
            tts_phrase = _build_tts_text(result)
            if tts_phrase and not self._mute_active:
                self._tts.speak(tts_phrase, result.risk_level)
                _last_tts_text = tts_phrase

            # 5-1: E2E 레이턴시 측정 (퓨전+오디오 결정 완료 시점, 신규 프레임 수신 시에만 갱신)
            if current_vision_ts is not None:
                e2e_ms = (time.monotonic() - current_vision_ts) * 1000.0
                if e2e_ms_ema == 0.0:
                    e2e_ms_ema = e2e_ms
                else:
                    e2e_ms_ema = (1 - _FPS_EMA_ALPHA) * e2e_ms_ema + _FPS_EMA_ALPHA * e2e_ms
                if e2e_ms_ema > config.LATENCY_WARN_THRESHOLD_MS:
                    logger.warning("E2E 레이턴시 초과: %.1fms (임계값 %.0fms)", e2e_ms_ema, config.LATENCY_WARN_THRESHOLD_MS)
                current_vision_ts = None  # 신규 프레임이 들어올 때만 재계산

            # 5-3: 카메라 가림 경고 발생
            now = time.monotonic()
            if _occlusion_counter >= config.CAMERA_OCCLUSION_FRAMES:
                if now - _last_occlusion_alert_time > config.CAMERA_OCCLUSION_COOLDOWN_SEC:
                    logger.warning(
                        "카메라 가림 감지 (%.0f 프레임 연속 픽셀 변화량 < %.1f)",
                        config.CAMERA_OCCLUSION_FRAMES,
                        config.CAMERA_OCCLUSION_CHANGE_THRESH,
                    )
                    if not self._mute_active:
                        self._audio.play_occlusion_alert()
                    _last_occlusion_alert_time = now

            now = time.monotonic()
            if now - last_log_time >= config.LOG_INTERVAL_SEC:
                if self._use_mock:
                    cpu_temp = round(40.0 + random.uniform(-5.0, 5.0), 1)
                elif self._use_hw:
                    cpu_temp = round(_read_cpu_temp(), 1)
                else:
                    cpu_temp = 0.0

                # 5-2: CPU 온도 기반 발열 스로틀링
                if self._use_hw:
                    if cpu_temp > config.THERMAL_THROTTLE_TEMP_C and not thermal_throttle_active:
                        logger.warning(
                            "CPU 온도 %.1f°C 초과, FPS 스로틀링 활성화 (%d FPS)",
                            cpu_temp,
                            config.THERMAL_THROTTLE_FPS,
                        )
                        self._thermal_event.set()
                        frame_interval = 1.0 / config.THERMAL_THROTTLE_FPS
                        thermal_throttle_active = True
                    elif cpu_temp <= config.THERMAL_RECOVERY_TEMP_C and thermal_throttle_active:
                        logger.info(
                            "CPU 온도 %.1f°C 복귀, 정상 FPS 복원 (%d FPS)",
                            cpu_temp,
                            config.TARGET_FPS,
                        )
                        self._thermal_event.clear()
                        frame_interval = 1.0 / config.TARGET_FPS
                        thermal_throttle_active = False

                try:
                    self._csv_logger.write_row(
                        tof_distance_cm=result.distance_cm,
                        alert_triggered=result.risk_level != RiskLevel.NONE,
                        fps=max(0, round(actual_fps)),
                        cpu_temp=cpu_temp,
                        latency_ms=round(e2e_ms_ema, 1),
                        tts_spoken=_last_tts_text,
                    )
                except Exception as exc:
                    logger.error("CSV 로그 기록 실패: %s", exc)
                _last_tts_text = ""
                last_log_time = now

                # 5-4: 배터리 잔량 확인 (30초 주기)
                if now - last_battery_check_time >= config.BATTERY_CHECK_INTERVAL_SEC:
                    pct = _read_battery_percent()
                    if pct is not None and pct < config.BATTERY_LOW_THRESHOLD_PCT:
                        logger.warning("배터리 잔량 부족: %d%%", pct)
                        self._beep.request_system_alert(RiskLevel.MID)
                    last_battery_check_time = now

            elapsed = time.monotonic() - loop_start
            time.sleep(max(0.0, frame_interval - elapsed))


def main() -> None:
    """RasEyes 진입점."""
    use_mock = os.getenv("RASEYES_MOCK", "0") == "1"
    use_hw = os.getenv("RASEYES_HW", "0") == "1"
    RasEyesApp(use_mock=use_mock, use_hw=use_hw).run()


if __name__ == "__main__":
    main()
