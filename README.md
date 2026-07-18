# RasEyes

시각장애인의 상단 사각지대(가슴~머리 높이) 장애물을 실시간으로 탐지하는 웨어러블 엣지 AI 디바이스.

흰지팡이가 감지하지 못하는 간판·나뭇가지·트럭 적재함 등의 충돌 위험을 카메라 비전 AI와 ToF 거리 센서로 탐지하고, 3.5mm 이어폰 잭을 통해 비프음과 음성(TTS)으로 즉각적인 청각 피드백을 제공합니다.

---

## 핵심 KPI

| 지표 | 목표 |
|------|------|
| End-to-End Latency | < 500ms |
| 비전 추론 속도 | < 60ms (15+ FPS) |
| 탐지 Recall | > 95% |
| 오탐지율 | < 1회 / 분 |

---

## 시스템 구조

```
Camera ──► Vision Module (YOLOv8 Nano)    ──┐
                                              ├──► Fusion Engine ──► Audio Feedback (비프음 + TTS)
ToF    ──► Distance Filter (이동평균)       ──┘         │
                                                      └──► CSV Logger
```

**센서 퓨전 판단 로직**

| 조건 | 위험 등급 | 동작 |
|------|-----------|------|
| 객체 탐지 & 거리 ≤ 100cm & Confidence ≥ 0.4 | High Risk | 즉각 경고음 (200ms 주기) + TTS |
| 객체 탐지 & 거리 ≤ 150cm | Mid Risk | 주의 경고음 (500ms 주기) + TTS |
| Confidence < 0.4 (저조도) | Fallback | ToF 단독 모드 전환 |

---

## 프로젝트 구조

```
RasEyes/
├── main.py                     # 오케스트레이션 (파이프라인 연결·스레드 조정)
├── config.py                   # 전역 상수 및 임계값
├── vision/
│   ├── interface.py            # VisionInterface / BaseCameraHAL 추상 클래스
│   ├── csi_camera_hal.py       # CSICameraHAL — Orange Pi 5 MIPI CSI 카메라 (OV13855)
│   ├── opencv_camera.py        # OpenCVCamera — 일반 USB 웹캠 HAL 구현체
│   ├── rknn_detector_hal.py    # RknnDetector — YOLOv8 Nano RKNN NPU 추론
│   ├── yolo_detector_hal.py    # YoloDetector — YOLOv8 Nano CPU 추론 (PC 검증용)
│   ├── mock.py                 # MockVision — PC 테스트용 Mock 구현체
│   └── mock_camera.py          # MockCamera — 빈 프레임 / 이미지 순환
├── sensor/
│   ├── interface.py            # BaseToFHAL 추상 클래스
│   ├── vl53l1x_hal.py          # VL53L1XHAL — 실제 ToF 센서 (I2C)
│   ├── filters.py              # MovingAverageFilter (window=3)
│   ├── button_handler.py       # ButtonHandler — 물리 버튼(음소거 토글) GPIO 입력
│   └── mock.py                 # MockToFSensor — 고정값·시퀀스 지원
├── fusion/
│   └── engine.py                # FusionEngine (퓨전 로직 + reset_filter)
├── audio/
│   ├── interface.py             # BaseAudioHAL / BaseTtsHAL 추상 클래스
│   ├── jack_hal.py              # JackAudioHAL — 3.5mm 잭 비프음 출력 (ALSA)
│   ├── resident_stream.py       # ResidentAudioStream — 상주 오디오 스트림 (전류 스파이크 방지)
│   ├── piper_tts.py             # PiperTts — 신경망 TTS (1순위)
│   ├── tts.py                   # EspeakTts — espeak-ng 기반 TTS (fallback)
│   ├── prerendered_tts.py       # 고정 경고 문구 사전 렌더링 캐시 로더
│   ├── beep_controller.py       # BeepController — 쿨다운 기반 경보 주기 제어
│   ├── boot_sequence.py         # 부팅 멜로디 + 안내 음성 재생
│   └── mock.py / mock_tts.py    # MockAudio / MockTts — PC 테스트용 Mock 구현체
├── logs/
│   └── logger.py                # CsvLogger — 1초 1회 CSV 기록
├── scripts/                     # RKNN 모델 변환, 벤치마크, TTS 모델/캐시 준비 유틸
└── tests/                       # pytest 스위트 (150개 케이스)
```

HAL(Hardware Abstraction Layer) 인터페이스를 통해 PC Mock 구현체와 Orange Pi 5 하드웨어 구현체를 코드 변경 없이 교체할 수 있습니다.

---

## 개발 환경 설정

**개발 PC 요구 사항:** Python 3.13, Linux/macOS (GPU 가속 불필요 — CPU로 개발/검증, 실제 추론은 Orange Pi 5 NPU에서 수행)

```bash
# 1. 저장소 클론
git clone https://github.com/donghyun1209/RasEyes.git
cd RasEyes

# 2. 가상환경 생성 및 활성화
python3 -m venv .venv
source .venv/bin/activate

# 3. 의존성 설치
pip install -r requirements.txt
```

---

## 실행

```bash
# Mock 모드 — 카메라·모델 없이 전체 파이프라인 실행
RASEYES_MOCK=1 python main.py

# 기본 실행 — 실제 카메라·모델 필요
python main.py

# Orange Pi 5 HW HAL 사용 (배포 환경, 초기화 실패 시 자동 fallback)
RASEYES_HW=1 python main.py
```

실행 시 `logs/raseyes_log.csv`에 1초 1회 상태(timestamp, cpu_temp, fps, tof_distance_cm, alert_triggered)가 기록됩니다.

---

## 테스트

```bash
# 전체 테스트 실행 (현재 150개 통과)
pytest

# 커버리지 포함
pytest --cov=. --cov-report=term-missing

# 퓨전 로직 단위 테스트만
pytest tests/test_fusion.py
```

핵심 테스트 케이스: 거리 임계값 경계 조건, Low-light Fallback 전환, FPS Fallback 통합, 이동평균 필터 노이즈 평활화, TTS 선점/쿨다운 로직.

---

## 타겟 하드웨어

| 구성 요소 | 사양 |
|-----------|------|
| 컴퓨트 | Orange Pi 5 (4GB, RK3588S + NPU) |
| 카메라 | OV13855 MIPI CSI 카메라 |
| 거리 센서 | VL53L1X (ToF, I2C) |
| 오디오 출력 | 3.5mm 이어폰 잭 (ES8388 코덱, ALSA) |
| AI 모델 | YOLOv8 Nano (RKNN INT8 양자화, NPU 추론) |
| 부가 입력 | 물리 버튼 (GPIO, 음소거 토글) |

100% On-device 처리 — 외부 API 및 클라우드 연동 없음.

---

## 개발 로드맵

| Phase | 내용 | 상태 |
|-------|------|------|
| 0 | 프로젝트 기반 구축 | ✅ 완료 |
| 1 | PC Mock 파이프라인 완성 | ✅ 완료 |
| 2 | YOLOv8 비전 AI 통합 | ✅ 완료 |
| 3 | pytest 테스트 스위트 구축 | ✅ 완료 |
| 4 | Orange Pi 5 하드웨어 이식 | ✅ 완료 |
| 5 | 시스템 최적화 및 안정화 | ✅ 완료 |
| 6 | PoC 검증 및 베타 테스트 | ✅ 완료 (6-2 CSV 분석은 2.0으로 이관) |
| 7 | TTS 통합 (카메라 정보 음성 전달) | ✅ 완료 |

자세한 내용은 [1.0_ROADMAP.md](docs/1.0_ROADMAP.md)·[2.0_ROADMAP.md](docs/2.0_ROADMAP.md), 개발 규칙/배포 절차는 [CLAUDE.md](CLAUDE.md)를 참고하세요.
