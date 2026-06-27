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
FPS_FALLBACK_THRESHOLD: int = 10

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
TOF_I2C_PORT: int = 5                          # i2c-5 (I2C5_M3 overlay)
TOF_I2C_ADDRESS: int = 0x29                    # VL53L1X 기본 I2C 주소
TOF_TIMING_BUDGET_US: int = 50_000             # 50ms: 속도/정확도 균형
TOF_INTER_MEASUREMENT_MS: int = 50             # 측정 간격 (ms)
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
CAMERA_OCCLUSION_CHANGE_THRESH: float = 3.0    # 프레임 간 평균 픽셀 변화량 임계값
CAMERA_OCCLUSION_FRAMES: int = 15              # 연속 프레임 수 (~1초 @ 15FPS)
CAMERA_OCCLUSION_COOLDOWN_SEC: float = 5.0     # 가림 경고 재발 방지 쿨다운 (초)

# 5-4: Battery Warning
BATTERY_LOW_THRESHOLD_PCT: int = 20            # 배터리 잔량 경고 임계값 (%)
BATTERY_CHECK_INTERVAL_SEC: float = 30.0       # 배터리 확인 주기 (초)
BATTERY_SYSFS_PATH: str = "/sys/class/power_supply/battery/capacity"
