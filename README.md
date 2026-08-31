# RasEyes

시각장애인의 상단 사각지대(가슴~머리 높이) 장애물을 실시간으로 탐지하는 웨어러블 엣지 AI 디바이스.

흰지팡이가 감지하지 못하는 간판·나뭇가지·트럭 적재함 등의 충돌 위험을 카메라 비전 AI와 ToF 거리 센서로 탐지하고, 3.5mm 이어폰 잭을 통해 비프음과 음성(TTS)으로 즉각적인 청각 피드백을 제공합니다.

탐지·추론·판단·발화는 **100% 기기 안에서** 처리합니다 (외부 API·클라우드 없음). 도보 경로 조회만 폰 앱에서 이루어지고, 기기는 짧은 지시 코드만 넘겨받습니다.

> ### ⚠️ 연구용 프로토타입입니다
> **실제 보행 보조 수단으로 신뢰해서는 안 되며, 테스트 목적으로만 사용하십시오.**
> 야간(저조도) 보행은 지원하지 않고, 탐지 Recall은 측정되지 않았습니다.

---

## 한눈에 보기

| 항목 | 내용 |
|---|---|
| **하는 일** | ① 머리 높이 장애물 경보 · ② 주변 둘러보기 요약 · ③ 폰 연동 도보 길 안내 |
| **동작 방식** | 카메라(YOLOv8n NPU 추론) + ToF 거리 센서 융합 → 비프음 / 음성 |
| **하드웨어** | Orange Pi 5 (RK3588S NPU) · OV13855 CSI 카메라 · VL53L1X ToF · 3.5mm 이어폰 |
| **코드 규모** | Python 파이프라인 + iOS(Swift) 앱 · pytest **365개 통과** |
| **개발 기간** | 2026-05 ~ 2026-08 (v1.0 → v2.2) |

---

## 세 가지 동작 모드

| 모드 | 트리거 | 동작 |
|---|---|---|
| **① 상시 장애물 경보** | 자동 (기본 동작) | 전방 장애물을 거리 등급별로 비프음 + 음성 경고 |
| **② 둘러보기** | 내장 전원 버튼 | 제자리에서 360° 회전하면 방향별로 무엇이 있는지 요약 발화 |
| **③ 도보 길 안내** | iOS 앱에서 목적지 검색 | BLE로 받은 지시 코드를 `"Turn right in 50 meters"`로 발화 |

세 모드는 **안전 우선순위**로 조정됩니다 — 장애물 경보는 길 안내를 항상 선점하고(끊긴 안내는 뒤에 재생), 둘러보기 중에는 길 안내를 보류합니다.

---

## 시스템 구조

```
입력                                처리                          출력
─────────────────────────────────────────────────────────────────────────────
카메라(CSI) ─► AE ─► AWB ─► YOLOv8n 추론(NPU) ─┐
                                               ├─► 퓨전 엔진 ─► 경보 정책 ─┐
ToF(VL53L1X) ─► RangeStatus 게이트 ─► 이동평균 ─┘  (위험 등급)   (이벤트화)  │
                                                       │                    ├─► 오디오 출력
iPhone 앱 ──BLE(GATT)──► 길안내 파서 ──────────────────┼────────────────────┘   (비프음 + TTS)
                                                       │
                                                       ├─► CSV 로거 (세션별 파일)
                                                       └─► 클립 레코더 (HIGH 경보 전후 JPEG)
```

### 퓨전 판단 로직

**"탐지 0개"와 "비전 신뢰 불가"는 다른 상태입니다.** 이 둘을 뭉뚱그렸을 때 빈 장면에서도 ToF 숫자만으로 경보가 나갔기 때문에, 세 갈래로 분리했습니다.

| 상황 | 조건 | 판정 |
|---|---|---|
| 유효 탐지 있음 | 거리 ≤ 100cm & Confidence ≥ 0.4 | **High** — 즉각 경고음 + TTS |
| 유효 탐지 있음 | 거리 ≤ 150cm | **Mid** — 주의 경고음 + TTS |
| 비전 정상 + 탐지 0개 | 거리 ≤ 100cm | **High만** (Mid는 억제 — 지면·담벼락 오경보 방지) |
| 비전 실명 (`vision_blind`) | 거리만으로 판정 | **ToF 단독 안전망** (암흑·화이트아웃·FPS 붕괴·비전 stall) |

경보는 '상태'가 아니라 **'이벤트'** 로 나갑니다 (`fusion/alert_policy.py`) — 위험 수준이 올라가는 순간에만 발화하고, 해제에는 히스테리시스를 걸어 임계값 근처 진동을 흡수합니다. 이 게이트 도입 전 야외 실측에서는 분당 181회 비프가 울렸습니다.

### ToF 거리값 신뢰 판정

VL53L1X는 **측정 대상이 없어도 0이 아니라 그럴듯한 숫자를 반환합니다.** 야외 실측에서 508샘플 전부가 무효인데 거리는 34~321cm로 나왔고, 그 대역이 경보 임계값 한복판이라 빈 공간에서 경보가 계속 나갔습니다. 허수값의 대역이 환경마다 달라(야외 중앙값 120.8cm / 실내 30.8cm) 거리 크기로는 거를 수 없으므로, ST 원본 API의 **`RangeStatus`를 ctypes로 직접 읽어 판정**합니다 (`sensor/vl53l1x_hal.py`).

---

## 프로젝트 구조

```
RasEyes/
├── main.py                     # 오케스트레이션 (파이프라인 연결·스레드 조정·모드 전환)
├── config.py                   # 전역 상수 및 임계값
├── vision/
│   ├── interface.py            # VisionInterface / BaseCameraHAL 추상 클래스
│   ├── csi_camera_hal.py       # CSICameraHAL — Orange Pi 5 MIPI CSI 카메라 (OV13855)
│   ├── auto_exposure.py        # AutoExposure — 자동 노출 제어 법칙 (순수 로직, 드라이버에 AE 없음)
│   ├── auto_white_balance.py   # AutoWhiteBalance — 소프트웨어 그레이월드 (녹색 캐스팅 보정)
│   ├── rknn_detector_hal.py    # RknnDetector — YOLOv8 Nano RKNN NPU 추론
│   ├── yolo_detector_hal.py    # YoloDetector — YOLOv8 Nano CPU 추론 (PC 검증용)
│   ├── opencv_camera.py        # OpenCVCamera — USB 웹캠 HAL (CSI 실패 시 fallback)
│   └── mock.py / mock_camera.py # PC 테스트용 Mock 구현체
├── sensor/
│   ├── interface.py            # BaseToFHAL 추상 클래스
│   ├── vl53l1x_hal.py          # VL53L1XHAL — ToF 센서 (I2C + RangeStatus 게이트)
│   ├── filters.py              # MovingAverageFilter (window=3, 신규 샘플에만 전진)
│   ├── power_button_handler.py # PowerButtonHandler — 전원 버튼(둘러보기) evdev 입력
│   ├── ble_nav_hal.py          # BleNavHAL — BlueZ D-Bus GATT 서버 (폰 → 기기 수신)
│   └── mock.py                 # MockToFSensor — 고정값·시퀀스 지원
├── fusion/
│   ├── engine.py               # FusionEngine — 위험 등급 판정 (비전 × ToF)
│   ├── alert_policy.py         # AlertPolicy — 위험 '상태'를 경보 '이벤트'로 변환
│   ├── scan.py                 # 둘러보기 요약 문장 조립 (방향별 묶기·거리 발화)
│   └── nav_parser.py           # BLE 지시 코드 → 영어 안내 문장
├── audio/
│   ├── interface.py            # BaseAudioHAL / BaseTtsHAL 추상 클래스
│   ├── jack_hal.py             # JackAudioHAL — 3.5mm 잭 비프음 출력 (ALSA)
│   ├── resident_stream.py      # ResidentAudioStream — 상주 스트림 (전류 스파이크 방지)
│   ├── piper_tts.py            # PiperTts — 신경망 TTS (1순위)
│   ├── tts.py                  # EspeakTts — espeak-ng 기반 TTS (fallback)
│   ├── prerendered_tts.py      # 고정 경고 문구 사전 렌더링 캐시 로더
│   ├── beep_controller.py      # BeepController — 쿨다운 기반 경보 주기 제어
│   ├── boot_sequence.py        # 부팅 멜로디 + 안내 음성 재생
│   └── mock.py / mock_tts.py   # PC 테스트용 Mock 구현체
├── logs/
│   ├── logger.py               # CsvLogger — 1초 1회, 세션마다 새 파일
│   └── clip_recorder.py        # HIGH 경보 전후 프레임을 JPEG 시퀀스로 저장
├── ios/                        # iOS 길 안내 앱 (Swift/SwiftUI)
│   ├── RasEyesNavApp.swift     # 앱 진입점
│   ├── ContentView.swift       # 지도·검색·경로 화면
│   ├── LocationManager.swift   # GPS 추적
│   ├── NavigationSession.swift # 턴바이턴 자동 진행 (60m 예고 / 15m 통과)
│   ├── RouteProvider.swift     # 경로 제공자 프로토콜 (TMAP ↔ 해외 교체 지점)
│   ├── TmapRouteProvider.swift # TMAP 보행자 경로 API + turnType 매핑
│   └── BLEManager.swift        # CoreBluetooth 송신
├── scripts/                    # RKNN 변환, 벤치마크, 로그 수집·분석, 센서 진단 유틸
└── tests/                      # pytest 스위트 (365개 케이스)
```

HAL(Hardware Abstraction Layer) 인터페이스를 통해 PC Mock 구현체와 Orange Pi 5 하드웨어 구현체를 코드 변경 없이 교체할 수 있습니다.

**판단 로직은 순수 함수로 분리합니다** — `_should_low_power`, `_should_fps_fallback`, `_update_luma_blind`, `_decide_nav_speech`, `AutoExposure.update`는 모두 I/O·시계 호출 없이 `(상태) → (상태)` 형태라 하드웨어 없이 PC에서 검증됩니다.

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
pip install -r requirements.txt        # PC/dev
pip install -r requirements-rpi.txt    # Orange Pi 5 배포 시 (Pi에서 실행)
```

iOS 앱은 Mac + Xcode가 필요합니다. TMAP API 키는 `ios/**/Secrets.swift`에 넣고 커밋하지 않습니다 (`Secrets.example.swift` 참고).

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

실행하면 `logs/raseyes_log_<타임스탬프>.csv`에 1초 1회 상태가 기록됩니다. **세션(프로세스 실행)마다 새 파일**을 만들고 기존 파일을 절대 덮어쓰지 않습니다.

기록 컬럼: `timestamp`, `cpu_temp`, `fps`, `tof_distance_cm`, `alert_triggered`, `latency_ms`, `tts_spoken`, `occlusion_alerts`, `alerts_emitted`, `tof_raw_cm`, `tof_only_ratio`, `frame_luma`, `no_detect_ratio`, `mid_suppressed`, `exposure`, `gain`

### 로그 분석

```bash
bash scripts/pull_logs.sh                          # Pi의 CSV·클립을 logs_archive/로 수집
python scripts/analyze_logs.py logs_archive/*.csv  # FPS·온도·경보 빈도 KPI 분석
python scripts/log_viewer.py                       # 브라우저 뷰어 (표준 라이브러리만 사용)
```

---

## 테스트

```bash
# 전체 테스트 실행 (현재 365개 통과)
pytest

# 커버리지 포함
pytest --cov=. --cov-report=term-missing

# 퓨전 로직 단위 테스트만
pytest tests/test_fusion.py
```

주요 회귀 테스트 — 각각 실제로 겪은 결함을 잠급니다.

| 파일 | 잠그는 결함 |
|---|---|
| `test_fusion.py` · `test_alert_policy.py` | 거리 임계값 경계, 경보 이벤트화·히스테리시스 |
| `test_fps_fallback.py` | TTS 발화 중 FPS 저하가 비전을 실명 처리하던 문제 |
| `test_low_power.py` | 저전력 모드 진동(분당 4.3회), 둘러보기 중 진입 차단 |
| `test_sensor.py` | ToF 재초기화 시 NULL 역참조 SEGV |
| `test_audio.py` | 믹서 음소거 해제 대상 오인(DAPM 위젯 vs 실제 스위치) |
| `test_nav.py` | 미지 지시 코드를 '직진'으로 뭉개던 문제, MID 경보가 길안내에 밀리던 문제 |
| `test_scan.py` | 둘러보기 방향별 묶기·상한·OoR 거리 생략 |

---

## 타겟 하드웨어

| 구성 요소 | 사양 |
|-----------|------|
| 컴퓨트 | Orange Pi 5 (4GB, RK3588S + NPU) |
| 카메라 | OV13855 MIPI CSI (`/dev/video11`, 센서 subdev `/dev/v4l-subdev2`) |
| 거리 센서 | VL53L1X (ToF, I2C `0x29`) — 정면 고정, 27° 원뿔 |
| 오디오 출력 | 3.5mm 이어폰 잭 (ES8388 코덱, ALSA card 2) |
| 무선 (개발) | WiFi 동글(ipTIME N150) + tailscale — 어느 망에서든 `ssh raseyes` |
| 무선 (길 안내) | USB BLE 동글 (Barrot BT5.3) — Orange Pi 5 기본형에는 온보드 BT가 없음 |
| 조작 | 내장 전원 버튼 1개 (둘러보기 트리거) |
| 냉각 | 액티브 쿨러 (`scripts/pwm_fan_control.py` — PWM 온도 제어) |
| AI 모델 | YOLOv8 Nano (RKNN INT8 양자화, NPU 추론) |
| 폰 | iOS (Swift/SwiftUI) — MapKit 검색 + TMAP 보행자 경로 |

---

## 핵심 KPI

| 지표 | 목표 |
|------|------|
| End-to-End Latency | < 500ms |
| 비전 추론 속도 | < 60ms (15+ FPS) |
| 탐지 Recall | > 95% |
| 오탐지율 | < 1회 / 분 |

---

## 개발 로드맵

| 버전 | 내용 | 문서 |
|---|---|---|
| **v1.0** | PC Mock 파이프라인 → YOLOv8 통합 → 테스트 스위트 → Orange Pi 5 이식 → 최적화 → PoC 검증 → TTS 통합 (Phase 0~7) | [1.0_ROADMAP.md](docs/1.0_ROADMAP.md) |
| **v2.0** | 유지보수·분석 인프라 — 무선(tailscale), 로그 수집·분석, 경고 클립 저장, 경보 정책, 자동 노출/화이트밸런스 | [2.0_ROADMAP.md](docs/2.0_ROADMAP.md) |
| **v2.1** | 둘러보기 모드, ToF RangeStatus 게이트, 저전력 모드 진동 수정, 로그 뷰어 | [2.1_ROADMAP.md](docs/2.1_ROADMAP.md) |
| **v2.2** | iOS 길 안내 앱, BLE GATT 서버, 안내 발화 통합(경보 우선순위) | [2.2_ROADMAP.md](docs/2.2_ROADMAP.md) |

요구사항·기술 명세는 [PRD.md](docs/PRD.md)·[TRD.md](docs/TRD.md), 개발 규칙과 배포 절차는 [CLAUDE.md](CLAUDE.md), 최신 작업 일지는 [ToPost.md](docs/ToPost.md)를 참고하세요.
