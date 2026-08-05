"""RasEyes 전역 상수 및 임계값."""
from typing import List

# Sensor Fusion Thresholds
HIGH_RISK_DIST_CM: int = 100
MID_RISK_DIST_CM: int = 150
MIN_CONFIDENCE: float = 0.4

# Alert Policy (엣지 트리거 + 히스테리시스)
# 위험 판정은 "거리 <= 임계값"인 상태라 그대로 두면 매 사이클 경보가 나간다.
# 2026-07-28 야외 실측: HIGH 상태 55.8% → 비프 분당 181회.
# 진입 시 1회만 울리고, 임계값+히스테리시스를 넘어야 해제한다.
ALERT_HYSTERESIS_CM: float = 30.0   # 해제 여유. 실측상 20→80cm로 키워도 감소율 81%→88%로 효과 미미
ALERT_REMINDER_SEC: float = 5.0     # 위험이 지속될 때 재알림 간격

# Signal Filtering
MOVING_AVG_WINDOW: int = 3

# Logging
LOG_INTERVAL_SEC: float = 1.0
# CSV는 세션마다 새 파일로 쓴다. 단일 파일에 mode="w"로 쓰던 방식은 재시작 때마다
# 이전 세션을 덮어써, 2026-07-28 야외 테스트 로그가 통째로 소실됐다.
LOG_DIR: str = "logs"
LOG_FILE_PREFIX: str = "raseyes_log"
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
FPS_FALLBACK_THRESHOLD: int = 8            # 미만이 연속되면 fallback 진입
# 해제선을 임계값보다 높게 잡아 경계 진동을 흡수한다. 다만 이 기기의 비전 FPS는
# 평상시 8 근처라(2026-08-04 야외 fallback 비율 34.5%), 해제선을 10 이상으로
# 올리면 한 번 걸린 fallback이 영영 안 풀려 진동보다 나쁜 상태가 된다.
# 관측된 비TTS 구간 최고치가 8.8~9.4라 9.0이 도달 가능한 상한이다.
FPS_FALLBACK_RECOVERY: float = 9.0         # 초과해야 해제 (히스테리시스)
FPS_FALLBACK_DEBOUNCE_FRAMES: int = 5      # 상태를 뒤집으려면 연속 N 사이클 필요

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
# ⚠️ 아래 CSI 경로와 csi_camera_hal._setup_isp_pipeline()의 media-ctl 엔티티 이름은
#    /boot/orangepiEnv.txt의 `overlays=... ov13855-c3` 설정에 종속된다.
#    카메라를 다른 CSI 포트로 옮기면 오버레이(c1/c2=i2c-7, c3=i2c-2)를 바꿔야 하고,
#    그에 따라 i2c 버스 번호가 박힌 엔티티 이름(`m02_b_ov13855 2-0036`)도 함께 바뀐다.
CSI_DEVICE_PATH: str = "/dev/video11"          # OV13855 MIPI CSI mainpath
CSI_SENSOR_SUBDEV: str = "/dev/v4l-subdev2"    # OV13855 V4L2 subdev (노출 제어용)
# AE 루프의 시드값. 예전에는 실내 최적값(3000/1984=게인 최대치)을 박아뒀는데,
# 그 상태로 야외에서 부팅하면 첫 수 초가 완전 화이트아웃이다. AE가 곧 잡으므로
# 한쪽 극단에 두지 않고 중간에서 출발한다.
CSI_SENSOR_EXPOSURE: int = 1500                # 부팅 초기 노출 시드 (max 3210)
CSI_SENSOR_GAIN: int = 128                     # 부팅 초기 아날로그 게인 시드 (min 128 / max 1984)
# 카메라 모듈이 상하 반전되어 장착됨. 센서에 flip 컨트롤이 없어 소프트웨어로 회전한다.
# 미적용 시 COCO 학습 YOLO가 거꾸로 선 사람을 놓치거나(conf 0.37) 오탐한다(laptop 0.42).
# Pi 실측 0.74ms/프레임 — 66ms(15FPS) 예산의 1.1%.
CSI_ROTATE_180: bool = True
TOF_I2C_PORT: int = 5                          # i2c-5 (I2C5_M3 overlay)
TOF_I2C_ADDRESS: int = 0x29                    # VL53L1X 기본 I2C 주소
TOF_TIMING_BUDGET_US: int = 200_000            # 200ms: MEDIUM mode 안정 동작 최소값
TOF_INTER_MEASUREMENT_MS: int = 210           # timing_budget + 10ms 마진
TOF_POLL_INTERVAL_SEC: float = 0.20            # 폴링 루프 대기 시간 (TOF_INTER_MEASUREMENT_MS에 맞춰 중복 조회 방지)
TOF_RANGING_MODE_SHORT: int = 1                # SHORT: 주변광 내성 최상, 최대 거리는 짧음 (실측 필요)
TOF_RANGING_MODE_MEDIUM: int = 2               # VL53L1X 레인징 모드 (2 = MEDIUM, 최대 3m, 200ms+ 타이밍 버짓 필요)
TOF_STALE_TIMEOUT_SEC: float = 1.0             # 이 시간 이상 값 갱신 없으면 센서 무응답으로 간주
TOF_MIN_VALID_CM: float = 4.0                  # VL53L1X 물리적 최소 측정 거리. 미만이면 무효 측정으로
                                               # 간주해 OoR 치환 (직사광 포화 시 0.1~1cm 쓰레기값이
                                               # 이동평균을 오염시켜 오경보 유발, 2026-08-04 실측)
# ToF 시야(ROI) 제한 — SPAD 16x16 격자 중 일부만 써서 지면 반사를 시야에서 배제한다.
# 근거: 2026-08-04 야외 86분 로그에서 유효 측정 2216개 중 2031개(92%)가 100~124cm
# 한 구간에 몰렸고 150cm 초과는 전체 샘플의 1.6%뿐이었다. FoV 27° 콘이라 가슴 높이에서
# 조금만 아래로 기울어도 지면이 걸린다 (docs/2.0_ROADMAP.md 5-4 마운트 각도 의심).
#
# ⚠️ **방향을 실측으로 확인하기 전에는 켜지 말 것.** SPAD 격자의 Y축이 실제 장면의
# 위/아래 중 어디에 대응하는지는 센서 광학 방향과 모듈 장착 방향에 달려 있어 코드로
# 판별할 수 없다 (카메라부터가 상하 반전 장착이라 CSI_ROTATE_180=True). 반대쪽을
# 자르면 머리 높이 장애물을 못 보게 되어 제품 전제 자체가 무너진다.
# 확인 절차: 서비스 정지 후 `python3 scripts/tof_roi_probe.py` (Pi에서, 착용 각도로).
#
# 좌표는 0~15, 최소 ROI는 4x4다. 기본값은 "상반부 절반(16x8)"에 해당하는 후보값이다.
TOF_ROI_ENABLED: bool = False                  # 프로브로 방향 확정 후 True
TOF_ROI_TOP_LEFT_X: int = 0
TOF_ROI_TOP_LEFT_Y: int = 15
TOF_ROI_BOT_RIGHT_X: int = 15
TOF_ROI_BOT_RIGHT_Y: int = 8
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

# === v2.0 Phase 3 — Event Clip Recording ===
CLIP_BUFFER_FPS: int = 5                       # 링 버퍼 적재 주기 (200ms 간격). 저전력 4FPS 대응을 위해 시간 기준 (프레임 카운트 금지)
CLIP_PRE_SEC: float = 5.0                      # 이벤트 전 보관 구간 (초)
CLIP_POST_SEC: float = 3.0                     # 이벤트 후 수집 구간 (초)
CLIP_COOLDOWN_SEC: float = 30.0                # 오버랩 억제 쿨다운. 불변식: 반드시 CLIP_POST_SEC보다 커야 함 (깨지면 후속 수집 중 재트리거 발생)
CLIP_JPEG_QUALITY: int = 85                    # JPEG 인코딩 품질. Pi 실측 3.15ms/프레임 (640x480 노이즈 최악 케이스)
CLIP_RETENTION_MAX_DIRS: int = 30              # 보존 클립 최대 개수 (사람이 검토 가능한 양)
CLIP_RETENTION_MAX_AGE_DAYS: int = 7           # 보존 최대 기간 (일). 개수 한도와 AND 조건 — 행인 얼굴 자동 소멸
CLIP_DIR: str = "logs/events"                  # 클립 저장 루트 (배포 rsync 및 .gitignore 제외 대상)

# === v2.0 Phase 5-1 — Auto Exposure (AE) ===
# OV13855 드라이버에는 auto_exposure/white_balance_auto 컨트롤이 없고 rkaiq 3A 데몬도
# 미설치다 (2026-07-29 Pi 실측). exposure/analogue_gain 수동 컨트롤만 노출되어 있어
# AE 루프를 직접 돌린다. 2026-07-29 야외 실측에서 고정 노출이 화이트아웃을 일으켜
# 프레임당 탐지가 0.64 → 0.05건으로 붕괴한 것이 도입 이유다.
CSI_AE_ENABLED: bool = True                    # False면 시드값 고정 (야외 결과 이상 시 원인 격리용)
CSI_AE_TARGET_LUMA: float = 110.0              # 목표 평균 휘도 (0~255)
CSI_AE_TOLERANCE: float = 12.0                 # 데드밴드. 이 안에서는 v4l2 호출 자체를 하지 않는다 (헌팅 억제)
CSI_AE_UPDATE_INTERVAL_SEC: float = 0.30       # 측광·보정 주기
# 센서에 노출을 써도 2~3프레임 뒤에 반영된다. 그 사이 같은 방향으로 또 스텝을 밟으면
# 오버슛 → 헌팅이 된다. 적용 직후 이 시간 동안은 측광 자체를 건너뛴다.
CSI_AE_SETTLE_SEC: float = 0.25
# v4l2-ctl subprocess 타임아웃. _setup_isp_pipeline()의 5초를 그대로 쓰면 v4l2-ctl이
# 멈췄을 때 비전 워커가 5초 블로킹되어 VISION_STALL_THRESHOLD_SEC(2.0)를 넘긴다.
CSI_AE_CTRL_TIMEOUT_SEC: float = 0.5
CSI_AE_MAX_STEP_UP: float = 2.0                # 1회 최대 증광 배율
CSI_AE_MAX_STEP_DOWN: float = 4.0              # 1회 최대 감광 배율. 화이트아웃이 급성 실패라 감광을 더 크게 잡는다
# 평균 휘도만 보면 부분 화이트아웃에 속는다 (절반이 날아가도 평균은 타깃 근처).
# 게다가 평균은 255에서 포화해 실제 과노출 배율을 과소평가한다. 클리핑 비율이
# 한도를 넘으면 평균과 무관하게 고정 배율로 감광해 기하급수적으로 걷어낸다.
CSI_AE_CLIP_THRESH: int = 250                  # 이 값 이상을 클리핑 픽셀로 센다
CSI_AE_CLIP_LIMIT: float = 0.10                # 클리핑 픽셀 비율 한도
CSI_AE_CLIP_STEP: float = 0.4                  # 한도 초과 시 고정 감광 배율
# 저전력 모드 진입을 미룰지 판단하는 "AE가 급하다" 기준. 데드밴드를 그대로 쓰면
# 조도가 늘 변하는 야외에서 AE가 상시 조정 중이라 저전력이 영영 걸리지 않는다.
# 프레임이 실제로 못 쓸 수준으로 어긋났을 때만 FPS를 붙든다.
CSI_AE_URGENT_LUMA_ERROR: float = 50.0
CSI_AE_EXPOSURE_MIN: int = 4                   # 하드웨어 범위 (v4l2 exposure: 4 ~ 3210)
# 노출은 길수록 저노이즈지만 모션블러가 늘어난다. 보행 웨어러블이라 블러가 불리하므로
# 이 값을 낮추면 어두운 곳에서 게인(노이즈)으로 대신 채운다.
# 2026-08-04 야외 실측에서 하드웨어 상한(3210)까지 열려 있던 결과, 프레임 휘도는
# 정상(luma 104~127)인데 선명도 하위 프레임은 화이트밸런스를 보정해도 탐지가 0건이었다
# (상위 3장 4건 vs 하위 3장 0건). 상한을 절반으로 내려 블러를 걷어내고 부족분은
# 게인으로 채운다. 노출/게인은 CSV에 기록되므로 다음 세션에서 재튜닝한다.
CSI_AE_EXPOSURE_MAX: int = 1600
CSI_AE_GAIN_MIN: int = 128                     # 하드웨어 범위 (v4l2 analogue_gain: 128 ~ 1984)
CSI_AE_GAIN_MAX: int = 1984
CSI_AE_METER_WIDTH: int = 160                  # 측광용 다운샘플링 너비 (원본 640의 1/4)
CSI_AE_METER_HEIGHT: int = 120                 # 측광용 다운샘플링 높이 (원본 480의 1/4)

# === v2.0 Phase 5-4 — Auto White Balance (AWB) ===
# 드라이버에 red_balance/blue_balance 컨트롤이 없어(2026-07-29 Pi 실측) 소프트웨어로
# 보정한다. 2026-08-04 야외 클립에서 **회색이어야 할 보도블록 패치**의 채널비가
# R/G=0.56~0.65, B/G=0.65~0.84로 측정됐다 — 장면의 자연 초록이 아니라 채널 게인
# 미보정이다. 이미지 구조(건물·차량 윤곽)는 정상이라 디모자이크 문제도 아니다.
# 같은 클립에 그레이월드를 걸고 PC에서 YOLO를 돌리면 탐지가 2건 → 5건으로 늘었다.
CSI_AWB_ENABLED: bool = True                   # False면 무보정 (원인 격리용)
CSI_AWB_UPDATE_INTERVAL_SEC: float = 0.50      # 게인 재계산 주기. 적용(LUT)은 매 프레임
# 게인을 측정값으로 즉시 갈아끼우면 장면이 바뀔 때마다 색이 출렁인다. EMA로 섞는다.
CSI_AWB_SMOOTHING: float = 0.25                # 신규 측정값 반영 비율 (0~1, 낮을수록 느리고 안정)
# 그레이월드는 "장면 평균은 무채색"을 가정하므로 잔디밭처럼 한 색이 지배하면 과보정한다.
# 클램프가 그 상한선이다. 실측 보정폭(R 약 1.8배)에 여유를 둔 값.
CSI_AWB_GAIN_MIN: float = 0.5
CSI_AWB_GAIN_MAX: float = 3.0
# 통계에서 제외할 밝기 대역. 클리핑된 픽셀은 세 채널이 모두 255라 게인 추정을 망치고,
# 암부는 노이즈가 지배해 색 정보가 없다.
CSI_AWB_DARK_THRESH: int = 16
CSI_AWB_BRIGHT_THRESH: int = 240
CSI_AWB_MIN_VALID_RATIO: float = 0.10          # 유효 픽셀이 이보다 적으면 게인을 갱신하지 않는다
CSI_AWB_METER_WIDTH: int = 160                 # 측광용 다운샘플링 너비
CSI_AWB_METER_HEIGHT: int = 120                # 측광용 다운샘플링 높이

# === v2.0 Phase 5-2 — 비전 신뢰 불가(vision_blind) 판정 ===
# "탐지 0개"와 "카메라 실명"을 구분한다. 예전에는 max_conf < MIN_CONFIDENCE 하나로
# 뭉뚱그렸는데, 디텍터가 이미 conf로 필터링하므로 그 식은 len(detections)==0과
# 동치였다. 그 결과 빈 장면에서도 객체 확인 게이트가 우회되어 ToF 숫자만으로 경보가
# 나갔다 (2026-07-29 야외 tof_only_ratio 96.2%).
# 밴드를 벗어나면 실명으로 본다 — 암흑과 화이트아웃 둘 다 카메라가 못 보는 상태다.
VISION_BLIND_LUMA_MIN: float = 25.0
VISION_BLIND_LUMA_MAX: float = 235.0
# AE 수렴 과도기(1~2초)에 휘도가 밴드를 들락거리면 모드가 깜빡여 MID 경보가 되살아난다.
# 진입/해제 모두 연속 프레임을 요구하고, 해제선에 여유를 둬 경계 진동을 흡수한다.
VISION_BLIND_DEBOUNCE_FRAMES: int = 5
VISION_BLIND_HYSTERESIS_LUMA: float = 15.0
