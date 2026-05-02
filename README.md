# RasEyes

시각장애인의 상단 사각지대(가슴~머리 높이) 장애물을 실시간으로 탐지하는 웨어러블 엣지 AI 디바이스.

흰지팡이가 감지하지 못하는 간판·나뭇가지·트럭 적재함 등의 충돌 위험을 카메라 비전 AI와 ToF 거리 센서로 탐지하고, 골전도 이어폰을 통해 즉각적인 청각 피드백을 제공합니다.

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
Camera ──► Vision Module (YOLOv8 Nano)  ──┐
                                           ├──► Fusion Engine ──► Audio Feedback
ToF    ──► Distance Filter (이동평균)    ──┘         │
                                                     └──► CSV Logger
```

**센서 퓨전 판단 로직**

| 조건 | 위험 등급 | 동작 |
|------|-----------|------|
| 객체 탐지 & 거리 ≤ 100cm & Confidence ≥ 0.4 | High Risk | 즉각 경고음 (200ms 주기) |
| 객체 탐지 & 거리 ≤ 150cm | Mid Risk | 주의 경고음 (500ms 주기) |
| Confidence < 0.4 (저조도) | Fallback | ToF 단독 모드 전환 |

---

## 프로젝트 구조

```
RasEyes/
├── main.py            # 오케스트레이션 (파이프라인 연결만 담당)
├── config.py          # 전역 상수 및 임계값
├── vision/
│   ├── interface.py   # VisionInterface HAL 추상 클래스
│   └── mock.py        # PC 테스트용 Mock 구현체
├── sensor/
│   ├── interface.py   # ToFSensorInterface HAL 추상 클래스
│   └── mock.py        # PC 테스트용 Mock 구현체
├── fusion/
│   └── engine.py      # 센서 퓨전 엔진 (핵심 비즈니스 로직)
├── audio/
│   ├── interface.py   # AudioInterface HAL 추상 클래스
│   └── mock.py        # PC 테스트용 Mock 구현체
├── logs/              # CSV 로그 저장 디렉터리
└── tests/             # pytest 테스트 스위트
```

HAL(Hardware Abstraction Layer) 인터페이스를 통해 현재 PC Mock 구현체와 추후 RPi 하드웨어 구현체를 코드 변경 없이 교체할 수 있습니다.

---

## 개발 환경 설정

**요구 사항:** Python 3.11, macOS (Apple Silicon 권장)

```bash
# 1. 저장소 클론
git clone https://github.com/donghyun1209/RasEyes.git
cd RasEyes

# 2. 가상환경 생성 및 활성화
python3.11 -m venv .venv
source .venv/bin/activate

# 3. 의존성 설치
pip install -r requirements.txt
```

---

## 실행

```bash
# Mock 모드로 전체 파이프라인 실행
python main.py
```

실행 시 `logs/raseyes_log.csv`에 1초 1회 상태가 기록됩니다.

---

## 테스트

```bash
# 전체 테스트 실행
pytest

# 커버리지 포함
pytest --cov=. --cov-report=term-missing
```

핵심 테스트 케이스: 거리 임계값 경계 조건, Low-light Fallback 전환, 오탐지 시나리오

---

## 타겟 하드웨어

| 구성 요소 | 사양 |
|-----------|------|
| 컴퓨트 | Raspberry Pi 5 (8GB) |
| 스토리지 | NVMe SSD 128GB (PCIe HAT) |
| 카메라 | Camera Module 3 |
| 거리 센서 | VL53L1X (ToF, I2C) |
| 오디오 출력 | 블루투스 골전도 이어폰 + GPIO 비상 부저 |
| AI 모델 | YOLOv8 Nano (TFLite INT8 양자화) |

100% On-device 처리 — 외부 API 및 클라우드 연동 없음.

---

## 개발 로드맵

| Phase | 내용 | 상태 |
|-------|------|------|
| 0 | 프로젝트 기반 구축 | ✅ 완료 |
| 1 | PC Mock 파이프라인 완성 | 🔲 진행 중 |
| 2 | YOLOv8 + MPS 비전 AI 통합 | 🔲 예정 |
| 3 | pytest 테스트 스위트 구축 | 🔲 예정 |
| 4 | RPi 5 하드웨어 이식 | 🔲 예정 |
| 5 | 시스템 최적화 및 안정화 | 🔲 예정 |
| 6 | PoC 베타 테스트 | 🔲 예정 |

자세한 내용은 [ROADMAP.md](ROADMAP.md)를 참고하세요.
