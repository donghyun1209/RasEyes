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
from types import FrameType
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

import config
from audio.beep_controller import BeepController
from audio.boot_sequence import BootSequence
from audio.interface import BaseAudioHAL, BaseTtsHAL
from audio.mock import MockAudio
from audio.mock_tts import MockTts
from audio.piper_tts import PiperTts
from audio.tts import EspeakTts
from fusion.alert_policy import AlertPolicy
from fusion.engine import FusionEngine, FusionResult, RiskLevel
from fusion.scan import (
    ScanCapture,
    ScannedObject,
    azimuth_direction,
    build_scan_sentence,
    dedupe_captures,
    is_wall_reading,
    try_pair_capture,
)
from logs.clip_recorder import ClipRecorder
from logs.logger import CsvLogger
from sensor.interface import BaseNavHAL, BaseToFHAL
from sensor.mock import MockToFSensor
from sensor.power_button_handler import PowerButtonHandler
from vision.yolo_detector_hal import YoloDetector
from vision.interface import DetectionResult, VisionInterface
from vision.mock import MockVision
from vision.opencv_camera import OpenCVCamera

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

def _read_cpu_temp() -> float:
    """Orange Pi 5의 CPU 온도를 섭씨로 반환한다. 읽기 실패 시 0.0 반환."""
    try:
        with open(config.CPU_TEMP_SYSFS_PATH) as f:
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
            from vision.rknn_detector_hal import RknnDetector
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


def _build_nav_sensor(use_mock: bool, use_hw: bool) -> BaseNavHAL:
    """환경에 맞는 네비게이션 BLE 센서 컴포넌트를 생성한다."""
    from sensor.mock import MockNavSensor
    if use_mock or not use_hw:
        return MockNavSensor()
    try:
        from sensor.ble_nav_hal import BleNavHAL
        return BleNavHAL()
    except (ImportError, RuntimeError) as exc:
        logger.warning("BleNavHAL 초기화 실패, MockNavSensor fallback: %s", exc)
        return MockNavSensor()


def _build_audio(use_mock: bool, use_hw: bool) -> BaseAudioHAL:
    """환경에 맞는 오디오 컴포넌트를 생성한다."""
    if use_mock or not use_hw:
        return MockAudio()
    try:
        import sounddevice  # noqa: F401 — package availability check
        from audio.jack_hal import JackAudioHAL
        return JackAudioHAL()
    except Exception as exc:
        logger.warning("JackAudioHAL 초기화 실패 (sounddevice 없음?), MockAudio fallback: %s", exc)
        return MockAudio()


def _build_tts(use_mock: bool) -> BaseTtsHAL:
    """환경에 맞는 TTS 컴포넌트를 생성한다.

    우선순위: PiperTts → EspeakTts → MockTts

    Args:
        use_mock: True이면 즉시 MockTts를 반환한다.

    Returns:
        초기화된 TTS HAL 구현체.
    """
    if use_mock:
        return MockTts()

    device_idx = _find_audio_device()

    # 1순위: Piper TTS — 모델 파일 존재 여부 선행 확인 (이슈 5 수정)
    if os.path.exists(config.TTS_PIPER_MODEL_PATH):
        try:
            tts = PiperTts(model_path=config.TTS_PIPER_MODEL_PATH, device_idx=device_idx)
            logger.info("PiperTts 초기화 완료 (모델: %s)", config.TTS_PIPER_MODEL_PATH)
            return tts
        except Exception as exc:
            logger.warning("PiperTts 초기화 실패, EspeakTts fallback: %s", exc)
    else:
        logger.warning("Piper 모델 파일 없음 (%s), EspeakTts fallback", config.TTS_PIPER_MODEL_PATH)

    # 2순위: espeak-ng
    try:
        subprocess.run(
            ["espeak-ng", "--version"],
            capture_output=True,
            check=True,
            timeout=3,
        )
        return EspeakTts(device_idx=device_idx)
    except (FileNotFoundError, subprocess.CalledProcessError, OSError) as exc:
        logger.warning("espeak-ng 없음, MockTts fallback: %s", exc)
        return MockTts()


def _find_audio_device() -> Optional[int]:
    """오디오 출력 장치 인덱스를 반환한다.

    ~/.asoundrc의 dmix 설정이 default → ES8388(hw:2,0)로 라우팅하므로 None을 반환한다.
    None이면 sounddevice가 ALSA default를 사용해 dmix 소프트웨어 믹싱을 거친다.

    Returns:
        None (ALSA default 사용).
    """
    return None


_DIR_EN: dict = {"왼쪽": "on the left", "오른쪽": "on the right", "정면": "ahead"}


def _build_tts_text(result: FusionResult) -> Optional[str]:
    """FusionResult에서 TTS 발화 텍스트를 생성한다 (영어).

    Returns:
        발화할 문자열. NONE 위험 수준이면 None.
    """
    if result.risk_level == RiskLevel.NONE:
        return None
    if result.tof_only_mode:
        if result.risk_level == RiskLevel.HIGH:
            return "Danger! Obstacle ahead"
        return "Caution, obstacle"
    label = result.top_label or "obstacle"
    direction_en = _DIR_EN.get(result.direction or "정면", "ahead")
    if result.risk_level == RiskLevel.HIGH:
        dist = round(result.distance_cm)
        return f"Danger! {label}, {dist} centimeters, {direction_en}"
    return f"{label} {direction_en}"


def _should_fps_fallback(
    effective_fps: float,
    low_power: bool,
    thermal_throttle: bool,
    tts_active: bool,
    active: bool,
    streak: int,
) -> Tuple[bool, int]:
    """FPS 미달을 '고장'으로 보고 ToF 단독 모드로 전환할지 판단한다.

    저전력(DYNAMIC_FPS_LOW_POWER_FPS=4), 발열 스로틀링(THERMAL_THROTTLE_FPS=5),
    TTS 발화 중 페이싱(TTS_ACTIVE_VISION_FPS=8)은 **의도적으로** FPS를 낮춘
    상태이고 모두 FPS_FALLBACK_THRESHOLD(8) 이하다. 이를 고장으로 취급하면
    FPS를 낮추는 순간 비전이 꺼져 모드가 스스로를 무력화한다 — 2026-07-28 Pi
    실측에서 저전력 진입 0.7초 뒤 ToF 단독 전환이 기록됐다. Fallback은 카메라
    멈춤·과부하 같은 **예기치 못한** FPS 붕괴만 잡는다.

    ⚠️ **TTS는 특히 위험하다.** 발화는 경보를 말하는 순간, 즉 장애물이 잡힌
    바로 그때 일어난다. 제외하지 않으면 경보마다 비전이 실명 처리된다 —
    2026-08-05 실측에서 발화 0.85~1.1초 뒤 fallback 진입이 매번 재현됐고
    (11:27:03.5 발화 → 11:27:04.3 진입) 분당 9회 진입/해제가 기록됐다.
    TTS 목표치는 임계값보다 낮은 게 아니라 **정확히 같아서**(둘 다 8) EMA 실측이
    경계를 오르내린 것이라 발견이 늦었다.

    저전력 4FPS는 250ms 간격으로 DATA_STALENESS_THRESHOLD_SEC(0.5초) 안이라
    탐지 결과는 그대로 유효하다.

    판정이 깜빡이면 곤란해 `_update_luma_blind`와 같은 두 겹을 건다:

    - **히스테리시스**: 일단 fallback에 걸리면 FPS_FALLBACK_RECOVERY를 넘어야 풀린다.
    - **디바운스**: 상태를 뒤집으려면 연속 N 사이클 같은 판정이 나와야 한다.

    Args:
        effective_fps: 루프 FPS와 비전 FPS 중 작은 값.
        low_power: 저전력 모드 활성 여부.
        thermal_throttle: 발열 스로틀링 활성 여부.
        tts_active: TTS 발화(합성/재생) 중 여부.
        active: 현재 fallback 상태.
        streak: 현재 상태와 반대되는 관측이 연속으로 나온 횟수.

    Returns:
        (갱신된 fallback 상태, 갱신된 연속 카운터).
    """
    if low_power or thermal_throttle or tts_active:
        # 의도적 저FPS는 "관측"이 아니라 "판단하지 않는 구간"이다. 디바운스를
        # 태우지 않고 즉시 해제해, 저FPS 구간 진입이 곧바로 반영되게 한다.
        return False, 0

    threshold = (
        config.FPS_FALLBACK_RECOVERY if active else config.FPS_FALLBACK_THRESHOLD
    )
    observed = effective_fps < threshold

    if observed == active:
        return active, 0
    streak += 1
    if streak >= config.FPS_FALLBACK_DEBOUNCE_FRAMES:
        return observed, 0
    return active, streak


def _update_luma_blind(
    luma: Optional[float], blind: bool, streak: int
) -> Tuple[bool, int]:
    """프레임 밝기로 카메라 실명 여부를 갱신한다.

    암흑과 화이트아웃을 모두 실명으로 본다 — 어느 쪽이든 카메라가 장면을 보지
    못하는 상태다. 판정이 깜빡이면 곤란해 두 겹으로 막는다:

    - **히스테리시스**: 일단 실명으로 판정되면 밴드 안쪽 깊숙이 들어와야 풀린다.
      경계에 걸친 밝기가 진동하는 것을 흡수한다.
    - **디바운스**: 상태를 뒤집으려면 연속 N프레임 동안 같은 판정이 나와야 한다.
      AE 수렴 과도기(1~2초)에 모드가 왔다 갔다 하며 MID 경보가 되살아나는 것을 막는다.

    Args:
        luma: 이번 사이클의 평균 휘도. None이면(Mock 모드 등 측광 불가) 상태를 유지한다.
        blind: 현재 실명 판정 상태.
        streak: 현재 판정과 반대되는 관측이 연속으로 나온 횟수.

    Returns:
        (갱신된 실명 상태, 갱신된 연속 카운터).
    """
    if luma is None:
        return blind, streak

    if blind:
        # 해제하려면 밴드 안쪽으로 히스테리시스만큼 더 들어와야 한다
        observed = not (
            config.VISION_BLIND_LUMA_MIN + config.VISION_BLIND_HYSTERESIS_LUMA
            <= luma
            <= config.VISION_BLIND_LUMA_MAX - config.VISION_BLIND_HYSTERESIS_LUMA
        )
    else:
        observed = not (
            config.VISION_BLIND_LUMA_MIN <= luma <= config.VISION_BLIND_LUMA_MAX
        )

    if observed == blind:
        return blind, 0
    streak += 1
    if streak >= config.VISION_BLIND_DEBOUNCE_FRAMES:
        return observed, 0
    return blind, streak


def _should_low_power(
    distance_cm: float,
    ae_settled: bool,
    scan_active: bool,
    active: bool,
    streak: int,
) -> Tuple[bool, int]:
    """근접 물체가 없어 비전 워커를 저전력(4 FPS)으로 낮출지 판단한다.

    진입선(DYNAMIC_FPS_NO_OBSTACLE_DIST_CM=200cm)과 해제선
    (MID_RISK_DIST_CM=150cm)이 달라 히스테리시스 밴드가 이미 있지만,
    **RangeStatus 게이트 도입 이후 그 밴드가 무력해졌다.** 게이트가 무효 측정을
    OoR 대체값(TOF_OUT_OF_RANGE_CM=400cm)으로 바꾸므로 거리는 400과 유효 측정을
    오갈 뿐 150~200cm 구간에 들어오는 일이 드물다 — 2026-08-27 야외 실측에서
    진입 51회/해제 50회(11.9분, 분당 4.3회)가 기록됐고 해제 시 거리는 23~137cm로
    전부 밴드 아래였다. 그래서 `_update_luma_blind`·`_should_fps_fallback`과 같은
    **연속 프레임 디바운스**를 한 겹 더 건다.

    ⚠️ **디바운스는 진입에만 건다.** 저전력 중에는 메인 루프도
    DYNAMIC_FPS_LOW_POWER_FPS(4)로 내려가 한 사이클이 250ms다. 해제까지 N 사이클을
    요구하면 근접 물체 반응이 그만큼(8사이클=2초) 늦어져 안전을 깎는다. 물체가
    잡히면 즉시 복귀한다.

    ⚠️ **스캔 중에는 저전력을 끈다** (진입 차단 + 활성이면 즉시 해제). 4 FPS에서는
    비전 프레임 간격이 DATA_STALENESS_THRESHOLD_SEC(0.5초)를 넘어(2026-08-27 실측
    0.53초) `탐지 결과 초기화`가 연속 발생하고, 그러면 ToF와 짝지을 탐지가 없어
    회전 중 스치는 방향이 통째로 누락된다 — 8/25부터 원인 미상이던 둘러보기 발화
    누락 ③이 이것이었다. 같은 날 3회 실행에서 비전 만료 11회였던 회차만 캡처가
    2건이었고(나머지 두 회차는 만료 0회·캡처 8건) 그 회차만 뒤·왼쪽을 빠뜨렸다.
    스캔은 10초 남짓이라 전력 영향은 무시할 수 있다.

    Args:
        distance_cm: 필터링된 ToF 거리 (cm).
        ae_settled: AE가 수렴했는지 여부. 미수렴 중에는 진입을 미룬다.
        scan_active: 360° 스캔 진행 중 여부.
        active: 현재 저전력 모드 상태.
        streak: 진입 관측이 연속으로 나온 횟수.

    Returns:
        (갱신된 저전력 상태, 갱신된 연속 카운터).
    """
    if scan_active:
        return False, 0

    if active:
        return (distance_cm > config.MID_RISK_DIST_CM), 0

    # AE 수렴 중에는 진입을 미룬다 — 4 FPS에서는 프레임이 250ms 간격으로만 들어와
    # 수렴이 배로 느려지는데, 그 구간이 정확히 그늘→직사광 전환처럼 AE가 가장
    # 급한 순간이다.
    if not ae_settled:
        return False, 0

    if distance_cm <= config.DYNAMIC_FPS_NO_OBSTACLE_DIST_CM:
        return False, 0
    streak += 1
    if streak >= config.DYNAMIC_FPS_ENTER_DEBOUNCE_FRAMES:
        return True, 0
    return False, streak


def _scan_should_finalize(now: float, scan_start_ts: float) -> bool:
    """스캔 시작 후 SCAN_MAX_DURATION_SEC(안전 상한)가 지났는지 판정한다.

    정상 경로는 사용자가 버튼을 다시 눌러 종료하는 것이고(_on_scan_trigger), 이
    함수는 사용자가 종료를 잊었을 때의 안전망이다. now를 인자로 받는 순수 함수라
    실제로 기다리지 않고 테스트할 수 있다 (CLAUDE.md §4 — 시간 의존 로직은
    time.monotonic()을 내부에서 호출하지 않는다).

    Args:
        now: 현재 단조 시각 (초).
        scan_start_ts: 스캔이 시작된 단조 시각 (초).

    Returns:
        경과 시간이 SCAN_MAX_DURATION_SEC 이상이면 True.
    """
    return now - scan_start_ts >= config.SCAN_MAX_DURATION_SEC


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
    low_power_event: Optional[threading.Event] = None,
    tts_active_event: Optional[threading.Event] = None,
) -> None:
    """비전 캡처+탐지를 별도 스레드에서 실행하고 결과를 큐에 넣는다.

    Args:
        vision: 비전 HAL 인터페이스.
        stop_event: 종료 신호 이벤트.
        out_q: (timestamp, frame, detections) 튜플을 담는 출력 큐.
        heartbeat: heartbeat[0]을 매 이터레이션마다 현재 시각으로 갱신한다.
                   메인 루프의 Watchdog이 이 값을 확인하여 스레드 스톨을 감지한다.
        throttle_event: 세트되면 추론 후 추가 슬립으로 FPS를 낮춘다 (발열 제어용).
        low_power_event: 세트되면 추론 후 추가 슬립으로 FPS를 낮춘다 (근접 물체 없을 때
                         전력 절감용). throttle_event가 세트된 경우 발열 제어가 우선한다.
        tts_active_event: 세트되면 추론 후 추가 슬립으로 FPS를 낮춘다 (TTS 합성 중 CPU/NPU
                          동시 피크 완화용). throttle_event보다는 낮고 low_power_event보다는
                          높은 우선순위를 가진다.
    """
    consecutive_failures = 0
    while not stop_event.is_set():
        heartbeat[0] = time.monotonic()  # Watchdog 갱신 + E2E 레이턴시 기준점 (캡처+추론 시작 직전 시각)
        try:
            frame, detections = vision.get_frame_detections()
            _put_latest(out_q, (heartbeat[0], frame, detections))
            consecutive_failures = 0
            if throttle_event is not None and throttle_event.is_set():
                target_fps = config.THERMAL_THROTTLE_FPS
            elif tts_active_event is not None and tts_active_event.is_set():
                target_fps = config.TTS_ACTIVE_VISION_FPS
            elif low_power_event is not None and low_power_event.is_set():
                target_fps = config.DYNAMIC_FPS_LOW_POWER_FPS
            else:
                target_fps = None
            if target_fps is not None:
                sleep_time = max(0.0, (1.0 / target_fps) - (time.monotonic() - heartbeat[0]))
                time.sleep(sleep_time)
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
    out_q: "queue.Queue[Tuple[float, int, float]]",
    on_reinit: Optional[Callable[[], None]] = None,
) -> None:
    """ToF 거리 읽기를 별도 스레드에서 실행하고 결과를 큐에 넣는다.

    Args:
        sensor: ToF HAL 인터페이스.
        stop_event: 종료 신호 이벤트.
        out_q: (timestamp, sample_seq, distance_cm) 튜플을 담는 출력 큐.
            타임스탬프는 큐에 넣은 시각이라 중복 판별에 쓸 수 없다 — 이 워커는
            HAL 캐시를 TARGET_FPS(15Hz)로 재읽기하는데 실제 측정은 ~4.8Hz라
            같은 값이 반복해서 실린다. 소비자가 걸러낼 수 있도록 HAL의 샘플
            시퀀스를 함께 싣는다.
        on_reinit: 센서 재초기화 성공 시 호출할 콜백 (예: 필터 리셋).
    """
    interval = 1.0 / config.TARGET_FPS
    consecutive_failures = 0
    while not stop_event.is_set():
        try:
            distance = sensor.read_distance_cm()
            _put_latest(out_q, (time.monotonic(), sensor.sample_seq, distance))
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
        self._nav_sensor: BaseNavHAL = _build_nav_sensor(use_mock, use_hw)
        self._fusion = FusionEngine()
        self._alert_policy = AlertPolicy()
        self._audio: BaseAudioHAL = _build_audio(use_mock, use_hw)
        self._tts: BaseTtsHAL = _build_tts(use_mock)
        self._beep = BeepController()
        self._csv_logger = CsvLogger()
        self._clips = ClipRecorder()

        self._vision_q: queue.Queue = queue.Queue(maxsize=config.QUEUE_SIZE)
        self._sensor_q: queue.Queue = queue.Queue(maxsize=config.QUEUE_SIZE)
        self._stop_event = threading.Event()
        self._thermal_event = threading.Event()
        self._low_power_event = threading.Event()
        self._tts_active_event = threading.Event()
        self._vision_heartbeat: List[float] = [time.monotonic()]

        self._v_thread: Optional[threading.Thread] = None
        self._s_thread: Optional[threading.Thread] = None

        self._power_button_handler: Optional[PowerButtonHandler] = None

        self._scan_active: bool = False
        self._scan_start_ts: Optional[float] = None
        self._scan_captures: List[ScanCapture] = []
        # 벽 요약 — 스캔 전체에서 가장 가까운 "탐지 0개 + ToF 유효" 값 하나만 남긴다.
        # dedupe_captures처럼 인스턴스로 쪼개면 회전 중 탐지가 끊겼다 이어지며 같은
        # 벽이 여러 항목으로 나뉘어 최종 문장(최대 5개)을 다 차지한다(2026-08-12 실측).
        self._scan_wall_min_cm: Optional[float] = None
        self._scan_wall_elapsed: float = 0.0
        # 버튼 스레드/SIGUSR1 핸들러는 이 플래그만 세운다 — _scan_active 등 상태는
        # 메인 루프 스레드만 만져서 락 없이 동시성 문제를 없앤다 (2-A-3, 아래
        # _request_scan_trigger/_consume_scan_trigger 참고).
        self._scan_trigger_requested = threading.Event()

    def start(self) -> None:
        """모든 컴포넌트와 워커 스레드를 시작한다.

        use_hw=True인 경우 컴포넌트 기동 사이에 STARTUP_STAGGER_SEC만큼 지연을 두어
        NPU/카메라/센서/오디오 순간 전류 스파이크가 겹치지 않도록 한다
        (보조배터리 과전류 보호 트립 완화). 부팅 오디오 큐(TTS 포함)도 NPU 연속 추론이
        시작되기 전에 재생을 마쳐, TTS 합성 CPU 부하와 추론 부하가 겹치지 않게 한다.
        """
        stagger = config.STARTUP_STAGGER_SEC if self._use_hw else 0.0

        self._vision.start()
        time.sleep(stagger)
        self._sensor.start()
        self._nav_sensor.start()
        time.sleep(stagger)
        self._audio.start()
        self._csv_logger.open()
        self._clips.rotate()

        if self._use_hw:
            try:
                self._power_button_handler = PowerButtonHandler()
                self._power_button_handler.start(self._request_scan_trigger)
            except RuntimeError as exc:
                logger.warning("PowerButtonHandler 초기화 실패 (evdev 미설치?): %s", exc)
                self._power_button_handler = None

        mode = "Mock" if self._use_mock else "YoloDetector"
        logger.info("RasEyes 시작 (%s 모드, 병렬 스레드)", mode)
        time.sleep(stagger)
        BootSequence().play(self._audio, self._tts)
        if self._use_hw:
            # tts.speak()는 논블로킹이라 즉시 반환됨 — 백그라운드 합성(CPU)이
            # NPU 연속 추론과 겹치지 않도록 발화 완료까지 대기한다.
            deadline = time.monotonic() + config.STARTUP_TTS_WAIT_TIMEOUT_SEC
            while self._tts.is_speaking() and time.monotonic() < deadline:
                time.sleep(0.05)

        self._vision_heartbeat[0] = time.monotonic()
        self._v_thread = threading.Thread(
            target=_vision_worker,
            args=(self._vision, self._stop_event, self._vision_q, self._vision_heartbeat,
                  self._thermal_event, self._low_power_event, self._tts_active_event),
            daemon=True,
            name="vision-worker",
        )
        self._s_thread = threading.Thread(
            target=_sensor_worker,
            args=(self._sensor, self._stop_event, self._sensor_q, self._on_sensor_reinit),
            daemon=True,
            name="sensor-worker",
        )
        time.sleep(stagger)
        self._v_thread.start()
        self._s_thread.start()

    def _request_scan_trigger(self) -> None:
        """전원 버튼 스레드에서 호출된다 — 상태를 직접 만지지 않고 플래그만 세운다.

        ⚠️ 이 메서드가 _scan_active/_scan_start_ts를 직접 만지면 안 된다. 버튼은
        자신만의 스레드(power-button-handler)에서 콜백을 실행하는데, 그 스레드가
        메인 루프와 락 없이 같은 필드를 주고받으면 스캔 종료가 메인 루프의 스캔
        처리 중간에 끼어드는 경합이 생긴다 — 실제로 _scan_start_ts가 None이 된
        직후 메인 루프가 그 값을 참조해 TypeError로 프로세스가 죽는 경로가 있었다
        (2026-08-16 발견, docs/2.1_ROADMAP.md §2-A). 상태를 만지는 스레드를 메인
        루프 하나로 좁히면(_consume_scan_trigger) 락이 필요 없어진다.
        """
        self._scan_trigger_requested.set()

    def _consume_scan_trigger(self) -> None:
        """메인 루프 스레드에서만 트리거 요청을 소비해 스캔 상태를 전이시킨다.

        버튼 콜백·SIGUSR1 핸들러가 세운 플래그를 메인 루프가 매 사이클 확인한다.
        상태(_scan_active 등)를 만지는 유일한 스레드가 되어 락 없이 안전하다.
        """
        if self._scan_trigger_requested.is_set():
            self._scan_trigger_requested.clear()
            self._on_scan_trigger()

    def _on_scan_trigger(self) -> None:
        """360° 둘러보기 모드를 토글한다. 메인 루프 스레드에서만 호출해야 한다.

        한 번 누르면 시작, 스캔 중에 다시 누르면 그 자리에서 종료하며 그때까지
        누적된 결과를 한 번에 요약해 발화한다(계획 A). 사람마다 한 바퀴 도는
        속도가 달라 고정 시간으로 끝을 추측하는 대신, 사용자가 직접 끝을
        알려주는 쪽이 더 정확하다(2026-08-12 결정).
        """
        if self._scan_active:
            logger.info("스캔 트리거: 종료 (두 번째 누름)")
            self._finish_scan(time.monotonic())
            return
        logger.info("스캔 트리거: 시작")
        self._scan_active = True
        self._scan_start_ts = time.monotonic()
        self._scan_captures = []
        self._scan_wall_min_cm = None
        self._scan_wall_elapsed = 0.0
        self._tts.speak(config.SCAN_MODE_ANNOUNCEMENT, RiskLevel.HIGH)

    def _finish_scan(self, now: float) -> None:
        """스캔을 종료하고 수집된 캡처로 결과 문장을 조립해 발화한다.

        Args:
            now: 종료 시각(단조 시각) — 버튼 재입력 시점 또는 SCAN_MAX_DURATION_SEC
                안전 상한 도달 시점. 실제 회전에 걸린 시간을 방위각 환산 기준으로
                쓴다 — 사람마다 회전 속도가 달라 고정값을 쓰면 방위각이 실제와
                어긋난다 (config.py의 SCAN_MAX_DURATION_SEC 주석 참고).
        """
        actual_duration = max(now - self._scan_start_ts, 0.1)
        objects = dedupe_captures(self._scan_captures, actual_duration)
        if self._scan_wall_min_cm is not None:
            direction = azimuth_direction(self._scan_wall_elapsed, actual_duration)
            objects.append(ScannedObject("wall", self._scan_wall_min_cm, direction))
        sentence = build_scan_sentence(objects)
        logger.info(
            "스캔 종료: %.1fs 소요, 캡처 %d건 → 객체 %d개 → \"%s\"",
            actual_duration, len(self._scan_captures), len(objects), sentence,
        )
        # 방향별 상한(SCAN_MAX_ITEMS_PER_DIRECTION)으로 잘리기 전 전체 목록 — 기준
        # 물체가 실제로는 잡혔는데 같은 방향의 다른 물체에 밀려 발화에서 빠졌는지
        # 진단하기 위함 (2026-08-25).
        logger.info(
            "스캔 원본 객체(발화 전 전체): %s",
            ", ".join(
                f"{o.label}/{o.direction}/{o.distance_cm:.0f}cm"
                for o in sorted(objects, key=lambda o: o.distance_cm)
            ),
        )
        self._tts.speak(sentence, RiskLevel.HIGH)
        self._scan_active = False
        self._scan_start_ts = None
        self._scan_captures = []
        self._scan_wall_min_cm = None
        self._scan_wall_elapsed = 0.0
        # 스캔 동안 실제 위험 상태가 어떻게 흘렀는지 알 수 없으므로, 재개 시점에
        # 래치를 깨끗하게 리셋한다 (센서 재초기화 시 리셋하는 것과 같은 이유).
        self._alert_policy.reset()

    def _on_sensor_reinit(self) -> None:
        """ToF 센서 재초기화 직후 거리 기반 상태를 모두 초기화한다.

        필터 버퍼와 경보 래치 모두 재초기화 전 거리값을 기준으로 하고 있어,
        센서가 복구되면 값이 불연속으로 뛴다. 둘을 함께 리셋해야 한다.
        """
        self._fusion.reset_filter()
        self._alert_policy.reset()

    def stop(self) -> None:
        """모든 워커 스레드를 중단하고 컴포넌트를 정리한다."""
        self._stop_event.set()
        if self._power_button_handler is not None:
            self._power_button_handler.stop()
        if self._v_thread:
            self._v_thread.join(timeout=2.0)
        if self._s_thread:
            self._s_thread.join(timeout=2.0)
        self._vision.stop()
        self._sensor.stop()
        self._nav_sensor.stop()
        self._audio.stop()
        self._tts.stop()
        self._csv_logger.close()
        self._clips.close()
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
        def _on_sigterm(signum: int, frame: Optional[FrameType]) -> None:
            logger.info("종료 신호 수신 (SIGTERM)")
            self._stop_event.set()

        def _on_sigusr1(signum: int, frame: Optional[FrameType]) -> None:
            # 시그널 핸들러 안에서 상태를 직접 만지거나 tts.speak()를 부르면
            # 메인 루프 처리 중간에 재진입하는 위험이 있다 — 플래그만 세운다
            # (_request_scan_trigger와 같은 원칙, 2-A-3).
            logger.info("스캔 트리거 신호 수신 (SIGUSR1)")
            self._scan_trigger_requested.set()

        signal.signal(signal.SIGTERM, _on_sigterm)
        signal.signal(signal.SIGUSR1, _on_sigusr1)

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

        # 이벤트 클립용 최신 프레임 (큐 get 실패 시에도 참조되므로 명시적으로 초기화)
        last_frame: Optional[np.ndarray] = None
        last_frame_ts: Optional[float] = None

        # 데이터 최신성 추적용 타임스탬프 (None = 아직 데이터 없음)
        last_vision_ts: Optional[float] = None
        last_sensor_ts: Optional[float] = None
        last_sensor_seq: int = -1

        # 실측 FPS 상태 (메인 루프 + 비전 워커 별도 추적)
        actual_fps: float = float(config.TARGET_FPS)
        vision_fps: float = float(config.TARGET_FPS)
        prev_loop_start: float = time.monotonic()
        prev_vision_ts: Optional[float] = None
        fps_fallback_active: bool = False
        _fps_fallback_streak: int = 0
        _low_power_streak: int = 0

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
        _occlusion_frame_counter: int = 0
        _last_occlusion_alert_time: float = 0.0
        _occlusion_alert_count: int = 0

        # 5-4: 배터리 잔량 확인
        last_battery_check_time: float = 0.0

        # 7: TTS 로그 주기 내 마지막 발화 텍스트 추적
        _last_tts_text: str = ""

        # 5-2: 비전 신뢰 불가 판정 상태 (밝기 밴드 + 디바운스)
        frame_luma: Optional[float] = None
        vision_blind: bool = False
        _blind_streak: int = 0

        # 로그 주기별 진단 카운터 — 경보가 줄어든 것인지 센서가 실명한 것인지 구별하기 위함
        _alerts_emitted: int = 0
        _tof_only_cycles: int = 0
        _no_detect_cycles: int = 0
        _mid_suppressed: int = 0
        _total_cycles: int = 0

        while not self._stop_event.is_set():
            loop_start = time.monotonic()
            # 버튼/SIGUSR1이 세운 스캔 트리거 요청을 메인 루프 스레드에서만 소비한다
            # (2-A-3 동시성 가드 — _request_scan_trigger 문서 참고).
            self._consume_scan_trigger()

            # 실측 FPS 계산 (EMA 적용)
            iter_time = loop_start - prev_loop_start
            if iter_time > 0:
                actual_fps = (1 - config.FPS_EMA_ALPHA) * actual_fps + config.FPS_EMA_ALPHA / iter_time
            prev_loop_start = loop_start

            # 저전력/발열 스로틀 모드에 맞춰 메인 루프 프레임 예산 동적 조정
            _target_fps = config.TARGET_FPS
            if thermal_throttle_active:
                _target_fps = min(_target_fps, config.THERMAL_THROTTLE_FPS)
            if self._low_power_event.is_set():
                _target_fps = min(_target_fps, config.DYNAMIC_FPS_LOW_POWER_FPS)
            frame_interval = 1.0 / _target_fps

            # 비전 큐: 남은 프레임 예산만큼 블로킹 대기
            _vision_wait = max(0.0, frame_interval - (time.monotonic() - loop_start))
            try:
                vision_ts, last_frame, last_detections = self._vision_q.get(timeout=_vision_wait)
                current_vision_ts = vision_ts
                if prev_vision_ts is not None:
                    v_iter = vision_ts - prev_vision_ts
                    if v_iter > 0:
                        vision_fps = (
                            (1 - config.FPS_EMA_ALPHA) * vision_fps
                            + config.FPS_EMA_ALPHA / v_iter
                        )
                prev_vision_ts = vision_ts
                last_vision_ts = vision_ts
                last_frame_ts = vision_ts

                # 다운샘플 프레임 1장으로 밝기 측정과 가림 감지를 함께 처리한다.
                # 밝기는 매 사이클 필요하고(5-2 실명 판정) 가림은 N프레임에 1회면
                # 충분하지만, 리사이즈 비용은 한 번만 내면 된다.
                if not self._use_mock and last_frame is not None:
                    small_frame = cv2.resize(
                        last_frame,
                        (config.CAMERA_OCCLUSION_DOWNSCALE_WIDTH, config.CAMERA_OCCLUSION_DOWNSCALE_HEIGHT),
                    )
                    frame_luma = cv2.mean(cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY))[0]

                    # 5-3: 카메라 가림 감지 — N프레임마다 1회만 픽셀 변화량 분석 (CPU 절감)
                    _occlusion_frame_counter += 1
                    if _occlusion_frame_counter >= config.CAMERA_OCCLUSION_CHECK_INTERVAL_FRAMES:
                        _occlusion_frame_counter = 0
                        if _prev_frame is not None:
                            delta = cv2.mean(cv2.absdiff(small_frame, _prev_frame))[0]
                            if delta < config.CAMERA_OCCLUSION_CHANGE_THRESH:
                                _occlusion_counter += 1
                            else:
                                _occlusion_counter = 0
                        _prev_frame = small_frame
            except queue.Empty:
                pass
            # 큐가 비어 직전 거리를 재사용하거나, 같은 물리 측정이 다시 실려 온
            # 경우에는 필터를 전진시키지 않는다 (이동평균이 동일 샘플로 채워지는 것 방지)
            distance_is_new = False
            try:
                sensor_ts, sensor_seq, last_distance = self._sensor_q.get_nowait()
                last_sensor_ts = sensor_ts
                if sensor_seq != last_sensor_seq:
                    last_sensor_seq = sensor_seq
                    distance_is_new = True
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
                # 안전 거리로 되돌리려면 필터를 실제로 밀어야 한다 — 게이트에 걸려
                # 갱신되지 않으면 만료 이전의 거리가 그대로 남아 경보가 계속 나간다
                distance_is_new = True

            # 360° 스캔 모드: ToF 신규 샘플이 뜬 사이클에만 동기 캡처를 시도한다.
            # last_vision_ts/last_distance/distance_is_new/now는 위에서 이미 계산된 값을
            # 그대로 재사용하므로 추가 I/O가 없다. 정상 종료는 버튼 재입력
            # (_on_scan_trigger)이 담당하고, 여기서는 사용자가 종료를 잊었을 때의
            # 안전 상한만 본다.
            if self._scan_active:
                if distance_is_new:
                    capture = try_pair_capture(
                        self._scan_start_ts, last_vision_ts, now, last_detections,
                        last_distance, self._vision.conf_threshold,
                    )
                    if capture is not None:
                        self._scan_captures.append(capture)
                    elif is_wall_reading(
                        last_vision_ts, now, last_detections, last_distance, self._vision.conf_threshold,
                    ) and (self._scan_wall_min_cm is None or last_distance < self._scan_wall_min_cm):
                        self._scan_wall_min_cm = last_distance
                        self._scan_wall_elapsed = last_vision_ts - self._scan_start_ts
                if _scan_should_finalize(now, self._scan_start_ts):
                    logger.warning("스캔: 종료 버튼 없이 안전 상한(%.0fs) 도달, 자동 종료", config.SCAN_MAX_DURATION_SEC)
                    self._finish_scan(now)

            # 다이내믹 FPS: 근접 물체가 없으면 비전 워커를 저전력 모드로 전환.
            # 판정은 _should_low_power가 담당한다 (AE 게이트·스캔 차단·디바운스).
            prev_low_power = self._low_power_event.is_set()
            next_low_power, _low_power_streak = _should_low_power(
                last_distance,
                self._vision.ae_settled,
                self._scan_active,
                prev_low_power,
                _low_power_streak,
            )
            if next_low_power and not prev_low_power:
                logger.info(
                    "전방 %.0fcm 이내 물체 없음, 저전력 모드 진입 (%d FPS)",
                    config.DYNAMIC_FPS_NO_OBSTACLE_DIST_CM,
                    config.DYNAMIC_FPS_LOW_POWER_FPS,
                )
                self._low_power_event.set()
            elif prev_low_power and not next_low_power:
                if self._scan_active:
                    logger.info(
                        "스캔 진행 중, 저전력 모드 해제 (%d FPS)", config.TARGET_FPS
                    )
                else:
                    logger.info(
                        "물체 근접 감지 (%.0fcm), 저전력 모드 해제 (%d FPS)",
                        last_distance,
                        config.TARGET_FPS,
                    )
                self._low_power_event.clear()

            # 비전 워커 Watchdog 체크
            vision_stalled = self._check_vision_stall()

            # FPS 기준 미달 시 ToF 단독 모드 Fallback (의도적 저FPS는 제외 — 헬퍼 참조)
            effective_fps = min(actual_fps, vision_fps)
            low_power = self._low_power_event.is_set()
            thermal_throttle = self._thermal_event.is_set()
            tts_active = self._tts_active_event.is_set()
            intentional_low_fps = low_power or thermal_throttle or tts_active
            prev_fallback = fps_fallback_active
            fps_fallback_active, _fps_fallback_streak = _should_fps_fallback(
                effective_fps,
                low_power,
                thermal_throttle,
                tts_active,
                fps_fallback_active,
                _fps_fallback_streak,
            )
            if fps_fallback_active and not prev_fallback:
                logger.warning(
                    "FPS 기준 미달 (루프 %.1f FPS, 비전 %.1f FPS < %d), ToF 단독 모드 전환",
                    actual_fps,
                    vision_fps,
                    config.FPS_FALLBACK_THRESHOLD,
                )
            elif prev_fallback and not fps_fallback_active:
                logger.info(
                    "FPS fallback 해제 (루프 %.1f FPS, 비전 %.1f FPS%s)",
                    actual_fps,
                    vision_fps,
                    ", 의도적 저FPS 구간" if intentional_low_fps else " — 정상 복귀",
                )
            if fps_fallback_active:
                last_detections = []

            # 5-2: 비전을 신뢰할 수 없는 상태를 하나로 합성한다.
            # 밝기가 밴드를 벗어난 경우(암흑·화이트아웃)뿐 아니라 FPS fallback과
            # 비전 stall도 포함해야 한다 — 두 경우 모두 위에서 last_detections를
            # 비우거나 낡은 값을 쓰고 있어서, "비전 정상 + 탐지 0개"로 오독하면
            # 비전이 죽은 바로 그 순간 MID 안전망이 꺼진다.
            vision_blind, _blind_streak = _update_luma_blind(
                frame_luma, vision_blind, _blind_streak
            )
            vision_unreliable = vision_blind or fps_fallback_active or vision_stalled

            result = self._fusion.evaluate(
                last_detections,
                last_distance,
                min_confidence=self._vision.conf_threshold,
                vision_blind=vision_unreliable,
                distance_is_new=distance_is_new,
            )

            # 시스템 경고(배터리 등)와 퓨전 결과를 병합해 단일 오디오 채널로 직렬화
            pending_system = self._beep.pop_system_alert()
            effective_risk = (
                pending_system
                if pending_system is not None and pending_system.value > result.risk_level.value
                else result.risk_level
            )

            # 위험 '상태'를 경보 '이벤트'로 변환 — 진입/승격 시 1회, 지속 시 리마인더만.
            # 시스템 경고는 장애물 경보가 아니므로 정책을 우회한다.
            decision = self._alert_policy.evaluate(result.risk_level, result.distance_cm, now)
            # 스캔 중에는 장애물 경보만 멈춘다 — 배터리 등 시스템 경고는 정책을 우회하므로
            # pending_system은 그대로 통과시킨다.
            obstacle_alert_allowed = decision.emit and not self._scan_active
            if obstacle_alert_allowed:
                _alerts_emitted += 1
            alert_gate_open = obstacle_alert_allowed or pending_system is not None

            _total_cycles += 1
            if result.tof_only_mode:
                _tof_only_cycles += 1
            if not last_detections:
                _no_detect_cycles += 1
            if result.mid_suppressed:
                _mid_suppressed += 1

            # TTS 발화 상태 1회 조회 — 비프음 suppress 및 NPU 스로틀링 이벤트에 공용 사용
            tts_speaking = self._tts.is_speaking()
            if tts_speaking != self._tts_active_event.is_set():
                if tts_speaking:
                    self._tts_active_event.set()
                else:
                    self._tts_active_event.clear()

            # TTS 발화 중에는 비프음을 suppresse — 두 aplay가 겹쳐 들리는 것을 방지.
            # 게이트가 닫혀 있으면 should_beep()을 호출하지 않는다 — 쿨다운 타이머가
            # 갱신되지 않아야 다음 경보가 지연 없이 즉시 통과한다.
            should_play_beep = (
                alert_gate_open
                and self._beep.should_beep(effective_risk)
                and not tts_speaking
            )
            if should_play_beep:
                self._audio.play_alert(effective_risk)

            # TTS: 탐지 결과 기반 음성 알림 (비프음과 독립적으로 논블로킹 동작)
            tts_phrase = _build_tts_text(result)
            if obstacle_alert_allowed and tts_phrase:
                self._tts.speak(tts_phrase, result.risk_level)
                _last_tts_text = tts_phrase

            # 5-1: E2E 레이턴시 측정 (퓨전+오디오 결정 완료 시점, 신규 프레임 수신 시에만 갱신)
            if current_vision_ts is not None:
                e2e_ms = (time.monotonic() - current_vision_ts) * 1000.0
                if e2e_ms_ema == 0.0:
                    e2e_ms_ema = e2e_ms
                else:
                    e2e_ms_ema = (1 - config.FPS_EMA_ALPHA) * e2e_ms_ema + config.FPS_EMA_ALPHA * e2e_ms
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
                    self._audio.play_occlusion_alert()
                    _last_occlusion_alert_time = now
                    _occlusion_alert_count += 1

            # Phase 2: 도보 경로(BLE) 지시 수신 확인 (TTS 발화는 Phase 3)
            nav_instruction = self._nav_sensor.get_latest_instruction()
            if nav_instruction is not None:
                logger.info(f"Nav Instruction Received: {nav_instruction}")

            # v2.0 3: 경고 이벤트 클립 — 링 버퍼 적재 및 HIGH 트리거
            # E2E 레이턴시 측정 이후에 두어 JPEG 인코딩 시간이 레이턴시 EMA에 섞이지 않게 한다.
            self._clips.offer_frame(
                now,
                last_frame,
                last_frame_ts,
                last_detections,
                last_distance,
                result.distance_cm,
            )
            # 트리거는 result.risk_level 기준 — effective_risk는 배터리 경고가 병합된 값이라
            # 장애물 이벤트가 아니다.
            if result.risk_level is RiskLevel.HIGH:
                self._clips.trigger(now, result)

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
                        thermal_throttle_active = True
                    elif cpu_temp <= config.THERMAL_RECOVERY_TEMP_C and thermal_throttle_active:
                        logger.info(
                            "CPU 온도 %.1f°C 복귀, 정상 FPS 복원 (%d FPS)",
                            cpu_temp,
                            config.TARGET_FPS,
                        )
                        self._thermal_event.clear()
                        thermal_throttle_active = False

                _exposure_gain = self._vision.exposure_gain
                try:
                    self._csv_logger.write_row(
                        tof_distance_cm=result.distance_cm,
                        alert_triggered=result.risk_level != RiskLevel.NONE,
                        fps=max(0, round(actual_fps)),
                        cpu_temp=cpu_temp,
                        latency_ms=round(e2e_ms_ema, 1),
                        tts_spoken=_last_tts_text,
                        occlusion_alerts=_occlusion_alert_count,
                        alerts_emitted=_alerts_emitted,
                        tof_raw_cm=last_distance,
                        tof_only_ratio=(
                            _tof_only_cycles / _total_cycles if _total_cycles else 0.0
                        ),
                        frame_luma=frame_luma,
                        no_detect_ratio=(
                            _no_detect_cycles / _total_cycles if _total_cycles else 0.0
                        ),
                        mid_suppressed=_mid_suppressed,
                        exposure=None if _exposure_gain is None else _exposure_gain[0],
                        gain=None if _exposure_gain is None else _exposure_gain[1],
                    )
                except Exception as exc:
                    logger.error("CSV 로그 기록 실패: %s", exc)
                _last_tts_text = ""
                _occlusion_alert_count = 0
                _alerts_emitted = 0
                _tof_only_cycles = 0
                _no_detect_cycles = 0
                _mid_suppressed = 0
                _total_cycles = 0
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
