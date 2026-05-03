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
