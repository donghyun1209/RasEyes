"""RasEyes 전역 상수 및 임계값."""
from typing import List

# Sensor Fusion Thresholds
HIGH_RISK_DIST_CM: int = 100
MID_RISK_DIST_CM: int = 150
MIN_CONFIDENCE: float = 0.4

# Signal Filtering
MOVING_AVG_WINDOW: int = 3

# Logging
LOG_INTERVAL_SEC: float = 1.0
LOG_FILE_PATH: str = "logs/raseyes_log.csv"
LOG_FLUSH_INTERVAL_ROWS: int = 10  # 이 행 수마다 1회 flush (디스크 I/O 전력 절감)

# Camera
FRAME_WIDTH: int = 640
FRAME_HEIGHT: int = 480
TARGET_FPS: int = 15
CAMERA_BUFFER_SIZE: int = 1     # cv2.CAP_PROP_BUFFERSIZE — 오래된 프레임 지연 방지

# Runtime Queues / Main Loop
QUEUE_SIZE: int = 2             # 비전/센서 워커 출력 큐 최대 크기 (최신값만 유지)
FPS_EMA_ALPHA: float = 0.2      # 실측 FPS EMA 평활화 계수

# Audio Feedback
AUDIO_HIGH_RISK_INTERVAL_MS: int = 200
AUDIO_MID_RISK_INTERVAL_MS: int = 500

# Performance Monitoring
DATA_STALENESS_THRESHOLD_SEC: float = 0.5
FPS_FALLBACK_THRESHOLD: int = 8

# Vision Model
YOLO_MODEL_PATH: str = "yolov8n.pt"

# Benchmark
BENCHMARK_WARMUP_FRAMES: int = 5

# Worker Thread Recovery
REINIT_MAX_RETRIES: int = 5
REINIT_DELAY_SEC: float = 1.0

# Vision Worker Watchdog
VISION_STALL_THRESHOLD_SEC: float = 2.0

# Dynamic FPS (보조배터리 전력 절감 — 근접 물체 없을 때 비전 추론 FPS 저하)
DYNAMIC_FPS_NO_OBSTACLE_DIST_CM: float = 200.0  # 이 거리 초과 시 저전력 모드 진입
DYNAMIC_FPS_LOW_POWER_FPS: int = 4              # 저전력 모드 목표 FPS (3~5 FPS 범위)

# TTS 합성 중 NPU 동시 부하 완화 (CPU 피크 분산)
TTS_ACTIVE_VISION_FPS: int = 8                  # TTS 발화 중 비전 추론 목표 FPS

# ToF Sensor Out-of-Range Handling
TOF_OUT_OF_RANGE_CM: float = 400.0   # VL53L1X 최대 유효 범위 초과 기준
OOR_SOFT_RESET_COUNT: int = 3        # 연속 OoR 횟수 이상이면 필터 소프트 리셋

# Startup Power Stagger (보조배터리 순간 전류 스파이크 완화, use_hw=True에서만 적용)
STARTUP_STAGGER_SEC: float = 1.5               # 컴포넌트 순차 기동 간 지연시간 (초)
STARTUP_TTS_WAIT_TIMEOUT_SEC: float = 5.0      # 부팅 TTS 발화 완료 대기 최대 시간 (초)

# === Phase 4 — Hardware (Orange Pi 5) ===
CSI_DEVICE_PATH: str = "/dev/video11"          # OV13855 MIPI CSI mainpath
CSI_SENSOR_SUBDEV: str = "/dev/v4l-subdev2"    # OV13855 V4L2 subdev (노출 제어용)
CSI_SENSOR_EXPOSURE: int = 3000                # 부팅 초기 수동 노출값 (max 3210)
CSI_SENSOR_GAIN: int = 1984                    # 부팅 초기 아날로그 게인 (max 1984)
TOF_I2C_PORT: int = 5                          # i2c-5 (I2C5_M3 overlay)
TOF_I2C_ADDRESS: int = 0x29                    # VL53L1X 기본 I2C 주소
TOF_TIMING_BUDGET_US: int = 200_000            # 200ms: MEDIUM mode 안정 동작 최소값
TOF_INTER_MEASUREMENT_MS: int = 210           # timing_budget + 10ms 마진
TOF_POLL_INTERVAL_SEC: float = 0.20            # 폴링 루프 대기 시간 (TOF_INTER_MEASUREMENT_MS에 맞춰 중복 조회 방지)
TOF_RANGING_MODE_MEDIUM: int = 2               # VL53L1X 레인징 모드 (2 = MEDIUM, 최대 3m, 200ms+ 타이밍 버짓 필요)
TOF_STALE_TIMEOUT_SEC: float = 1.0             # 이 시간 이상 값 갱신 없으면 센서 무응답으로 간주
RKNN_MODEL_PATH: str = "yolov8n.rknn"          # NPU 추론 모델 경로
RKNN_CORE_MASK: str = "NPU_CORE_0_1"           # RKNNLite NPU 코어 마스크 속성명
GPIO_BUTTON_PIN: int = 26                      # 물리 버튼 GPIO 핀 번호
GPIO_CHIP_PATH: str = "/dev/gpiochip1"         # 물리 버튼 gpiod 칩 장치 경로
CPU_TEMP_SYSFS_PATH: str = "/sys/class/thermal/thermal_zone0/temp"  # CPU 온도 sysfs 경로

# Audio Synthesis (sounddevice, 3.5mm jack)
AUDIO_SAMPLE_RATE: int = 44100
AUDIO_HIGH_FREQ_HZ: float = 2000.0             # HIGH risk 비프음 주파수
AUDIO_MID_FREQ_HZ: float = 1000.0              # MID risk 비프음 주파수
AUDIO_BEEP_DURATION_MS: int = 80               # 비프음 지속 시간
AUDIO_BEEP_VOLUME: float = 0.2                 # 비프음 게인 (클리핑 방지, Full Scale 대비 20%)
AUDIO_BEEP_LEADIN_MS: int = 120                # 비프음 앞 무음 구간 (ES8388 앰프/dmix 콜드스타트 워밍업 마진)
AUDIO_FADE_MS: int = 10                        # 비프음 클릭 방지 fade-in/out 길이

# ALSA / ES8388 Codec Control
ALSA_CARD_INDEX: str = "2"                     # ES8388 코덱 ALSA 카드 번호
ALSA_PCM_VOLUME: str = "128"                   # amixer 강제 PCM 볼륨값
AUDIO_UNMUTE_MAX_RETRIES: int = 3              # 강제 음소거 해제 검증 재시도 횟수
AUDIO_UNMUTE_RETRY_DELAY_SEC: float = 0.3      # 재시도 간 지연시간 (초)

# === Phase 5 — Optimization & Stabilization ===
# 5-1: E2E Latency
LATENCY_WARN_THRESHOLD_MS: float = 400.0       # E2E 400ms 초과 시 경고

# 5-2: Thermal Graceful Degradation
THERMAL_THROTTLE_TEMP_C: float = 80.0          # 스로틀 트리거 CPU 온도 (°C)
THERMAL_RECOVERY_TEMP_C: float = 75.0          # 스로틀 해제 복구 임계값 (히스테리시스 5°C)
THERMAL_THROTTLE_FPS: int = 5                  # 스로틀 시 목표 FPS

# 5-3: Camera Occlusion Detection
CAMERA_OCCLUSION_CHANGE_THRESH: float = 1.0    # 프레임 간 평균 픽셀 변화량 임계값 (다운샘플링 프레임 기준)
CAMERA_OCCLUSION_CHECK_INTERVAL_FRAMES: int = 5  # N프레임마다 1회만 가림 검사 수행 (CPU 절감)
CAMERA_OCCLUSION_DOWNSCALE_WIDTH: int = 160    # 가림 검사용 다운샘플링 너비 (원본 640의 1/4)
CAMERA_OCCLUSION_DOWNSCALE_HEIGHT: int = 120   # 가림 검사용 다운샘플링 높이 (원본 480의 1/4)
CAMERA_OCCLUSION_FRAMES: int = 3               # 연속 "검사 통과" 횟수 (5프레임 간격 x 3회 ≈ 1초 @ 15FPS)
CAMERA_OCCLUSION_COOLDOWN_SEC: float = 5.0     # 가림 경고 재발 방지 쿨다운 (초)
CAMERA_OCCLUSION_ALERT_FREQ_HZ: float = 800.0  # 가림 경보음 주파수
CAMERA_OCCLUSION_ALERT_BEEP_MS: int = 60       # 가림 경보 비프 지속 시간
CAMERA_OCCLUSION_ALERT_GAP_MS: int = 60        # 가림 경보 비프 간 무음 간격

# 5-4: Battery Warning
BATTERY_LOW_THRESHOLD_PCT: int = 20            # 배터리 잔량 경고 임계값 (%)
BATTERY_CHECK_INTERVAL_SEC: float = 30.0       # 배터리 확인 주기 (초)
BATTERY_SYSFS_PATH: str = "/sys/class/power_supply/battery/capacity"

# === Phase 7 — TTS (Text-to-Speech) ===
TTS_HIGH_COOLDOWN_SEC: float = 2.0             # HIGH 재발화 억제 쿨다운
TTS_MID_COOLDOWN_SEC: float = 4.0              # MID 재발화 억제 쿨다운
TTS_DIRECTION_LEFT_RATIO: float = 0.33         # bbox 중심 x/width < 0.33 → 왼쪽
TTS_DIRECTION_RIGHT_RATIO: float = 0.66        # bbox 중심 x/width > 0.66 → 오른쪽
TTS_ESPEAK_RATE: int = 160                     # espeak-ng 발화 속도 (wpm)
TTS_ESPEAK_VOICE: str = "en"                   # espeak-ng 영어 음성
TTS_PIPER_MODEL_PATH: str = "models/tts/en_US-lessac-medium.onnx"  # Piper 영어 모델 경로
TTS_ONNX_INTRA_OP_THREADS: int = 1             # ONNX Runtime 연산 내부 스레드 수 (전류 스파이크 방지)
TTS_ONNX_INTER_OP_THREADS: int = 1             # ONNX Runtime 연산 간 스레드 수 (전류 스파이크 방지)
TTS_PCM_CACHE_MAX_ENTRIES: int = 16            # 합성 PCM LRU 캐시 최대 항목 수 (반복 상용구 재합성 방지)
TTS_PRERENDERED_DIR: str = "models/tts/prerendered"  # 사전 렌더링된 고정 상용구 WAV 저장 경로
TTS_PRERENDERED_PHRASES: List[str] = [          # 빌드 타임에 미리 합성해둘 고정 경고 문구 (부팅 직후 부하 집중 완화)
    "Danger! Obstacle ahead",
    "Caution, obstacle",
    "RasEyes ready",
]
