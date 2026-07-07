"""RasEyes 전역 상수 및 임계값."""

# Sensor Fusion Thresholds
HIGH_RISK_DIST_CM: int = 100
MID_RISK_DIST_CM: int = 150
MIN_CONFIDENCE: float = 0.4

# Signal Filtering
MOVING_AVG_WINDOW: int = 3

# Logging
LOG_INTERVAL_SEC: float = 1.0
LOG_FILE_PATH: str = "logs/raseyes_log.csv"

# Camera
FRAME_WIDTH: int = 640
FRAME_HEIGHT: int = 480
TARGET_FPS: int = 15

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

# ToF Sensor Out-of-Range Handling
TOF_OUT_OF_RANGE_CM: float = 400.0   # VL53L1X 최대 유효 범위 초과 기준
OOR_SOFT_RESET_COUNT: int = 3        # 연속 OoR 횟수 이상이면 필터 소프트 리셋

# === Phase 4 — Hardware (Orange Pi 5) ===
CSI_DEVICE_PATH: str = "/dev/video11"          # OV13855 MIPI CSI mainpath
CSI_SENSOR_SUBDEV: str = "/dev/v4l-subdev2"    # OV13855 V4L2 subdev (노출 제어용)
CSI_SENSOR_EXPOSURE: int = 3000                # 부팅 초기 수동 노출값 (max 3210)
CSI_SENSOR_GAIN: int = 1984                    # 부팅 초기 아날로그 게인 (max 1984)
TOF_I2C_PORT: int = 5                          # i2c-5 (I2C5_M3 overlay)
TOF_I2C_ADDRESS: int = 0x29                    # VL53L1X 기본 I2C 주소
TOF_TIMING_BUDGET_US: int = 200_000            # 200ms: MEDIUM mode 안정 동작 최소값
TOF_INTER_MEASUREMENT_MS: int = 210           # timing_budget + 10ms 마진
RKNN_MODEL_PATH: str = "yolov8n.rknn"          # NPU 추론 모델 경로
GPIO_BUTTON_PIN: int = 26                      # 물리 버튼 GPIO 핀 번호

# Audio Synthesis (sounddevice, 3.5mm jack)
AUDIO_SAMPLE_RATE: int = 44100
AUDIO_HIGH_FREQ_HZ: float = 2000.0             # HIGH risk 비프음 주파수
AUDIO_MID_FREQ_HZ: float = 1000.0              # MID risk 비프음 주파수
AUDIO_BEEP_DURATION_MS: int = 80               # 비프음 지속 시간

# === Phase 5 — Optimization & Stabilization ===
# 5-1: E2E Latency
LATENCY_WARN_THRESHOLD_MS: float = 400.0       # E2E 400ms 초과 시 경고

# 5-2: Thermal Graceful Degradation
THERMAL_THROTTLE_TEMP_C: float = 80.0          # 스로틀 트리거 CPU 온도 (°C)
THERMAL_RECOVERY_TEMP_C: float = 75.0          # 스로틀 해제 복구 임계값 (히스테리시스 5°C)
THERMAL_THROTTLE_FPS: int = 5                  # 스로틀 시 목표 FPS

# 5-3: Camera Occlusion Detection
CAMERA_OCCLUSION_CHANGE_THRESH: float = 1.0    # 프레임 간 평균 픽셀 변화량 임계값
CAMERA_OCCLUSION_FRAMES: int = 15              # 연속 프레임 수 (~1초 @ 15FPS)
CAMERA_OCCLUSION_COOLDOWN_SEC: float = 5.0     # 가림 경고 재발 방지 쿨다운 (초)

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

# COCO 클래스 한국어 레이블 매핑. 미매핑 클래스는 영문 레이블 그대로 발화.
COCO_KO_LABELS: dict = {
    "person": "사람",
    "bicycle": "자전거",
    "car": "자동차",
    "motorcycle": "오토바이",
    "bus": "버스",
    "truck": "트럭",
    "traffic light": "신호등",
    "fire hydrant": "소화전",
    "stop sign": "정지 표지판",
    "bench": "벤치",
    "cat": "고양이",
    "dog": "강아지",
    "bird": "새",
    "backpack": "가방",
    "umbrella": "우산",
    "handbag": "핸드백",
    "suitcase": "캐리어",
    "bottle": "병",
    "cup": "컵",
    "chair": "의자",
    "couch": "소파",
    "dining table": "테이블",
    "toilet": "화장실",
    "tv": "텔레비전",
    "laptop": "노트북",
    "door": "문",
}
