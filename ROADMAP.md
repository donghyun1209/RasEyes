# RasEyes — Development Roadmap

> **목표:** 시각장애인용 상단 사각지대 장애물 탐지 웨어러블 엣지 AI 디바이스 PoC 완성  
> **최종 KPI:** E2E Latency < 500ms · 추론 < 60ms (15+ FPS) · Recall > 95% · 오탐지 < 1회/분

---

## Phase 0 · 프로젝트 기반 구축 ✅
> 목표: 개발 환경과 아키텍처 골격 확정

| # | 작업 | 산출물 |
|---|------|--------|
| 0-1 | 프로젝트 디렉터리 구조 확정 (`/vision`, `/sensor`, `/fusion`, `/audio`) | 폴더 구조 |
| 0-2 | `config.py` 작성 — 모든 임계값·상수 중앙화 | `config.py` |
| 0-3 | `CLAUDE.md` · `PRD.md` · `TRD.md` 작성 | 문서 |
| 0-4 | Python 3.11 venv 환경 구성 및 의존성 정의 | `requirements.txt` |

---

## Phase 1 · PC 모킹(Mock) 레이어 구현 ✅
> 목표: RPi 하드웨어 없이 전체 파이프라인을 PC에서 검증 가능하게 만들기

### 1-A. HAL(Hardware Abstraction Layer) 인터페이스 정의 ✅
| # | 작업 | 파일 | 상태 |
|---|------|------|------|
| 1-A-1 | `BaseCameraHAL` 추상 클래스 정의 (프레임 캡처 인터페이스) | `vision/hal.py` | ✅ |
| 1-A-2 | `BaseToFHAL` 추상 클래스 정의 (거리 읽기 인터페이스) | `sensor/hal.py` | ✅ |
| 1-A-3 | `BaseAudioHAL` 추상 클래스 정의 (비프음 재생 인터페이스) | `audio/hal.py` | ✅ |

> **참고:** 기존 `sensor/interface.py`, `audio/interface.py`는 후방 호환 re-export로 유지.

### 1-B. Mock 구현체 작성 ✅
| # | 작업 | 파일 | 상태 |
|---|------|------|------|
| 1-B-1 | `MockCamera` — 빈 프레임 / 로컬 이미지 파일을 순환 반환 | `vision/mock_camera.py` | ✅ |
| 1-B-2 | `MockToFSensor` — 고정값 또는 시나리오별 거리 시퀀스 생성 | `sensor/mock.py` | ✅ |
| 1-B-3 | `MockAudio` — 콘솔 로그로 경보 피드백 시뮬레이션 | `audio/mock.py` | ✅ |

### 1-C. 센서 퓨전 엔진 구현 (핵심 로직) ✅
| # | 작업 | 파일 | 상태 |
|---|------|------|------|
| 1-C-1 | ToF 이동 평균 필터 구현 (window=3) | `sensor/filters.py` | ✅ |
| 1-C-2 | 퓨전 규칙 구현 — High Risk / Mid Risk / Low-light Fallback | `fusion/engine.py` | ✅ |
| 1-C-3 | 거리에 따른 비프음 주기 가변 제어 로직 | `audio/beep_controller.py` | ✅ |

### 1-D. 메인 오케스트레이터 ✅
| # | 작업 | 파일 | 상태 |
|---|------|------|------|
| 1-D-1 | `main.py` — 병렬 비전·센서 스레드 루프 구성 | `main.py` | ✅ |
| 1-D-2 | CSV 로거 구현 (1초 1회 기록) | `logs/logger.py` | ✅ |

> **참고:** `CsvLogger` 클래스로 추출. `main.py`의 인라인 CSV 코드 제거. 비전·센서 각각 daemon thread로 분리, `queue.Queue(maxsize=2)` 를 통해 최신 값만 메인 루프에 전달.

**Phase 1 완료 기준:** Mock 환경에서 E2E 파이프라인이 콘솔로 정상 동작하고, 시나리오별 경고 로직이 의도대로 트리거됨. ✅ **달성** (pytest 46개 전체 통과)

---

## Phase 2 · 비전 AI 통합 (PC, MPS 가속) ✅
> 목표: Apple Silicon MPS에서 YOLOv8 Nano 추론 속도 ≥ 15 FPS 확인

| # | 작업 | 파일 | 상태 |
|---|------|------|------|
| 2-1 | YOLOv8 Nano 모델 다운로드 및 `torch.device("mps")` 설정 | `vision/detector.py` | ✅ |
| 2-2 | `CameraHAL`을 구현하는 실제 OpenCV 캡처 클래스 | `vision/opencv_camera.py` | ✅ |
| 2-3 | 추론 결과 → BBox + Confidence Score → 퓨전 엔진 연동 | `vision/detector.py` | ✅ |
| 2-4 | FPS 벤치마크 스크립트 작성 및 추론 지연 시간 측정 | `tests/benchmark_vision.py` | ✅ |
| 2-5 | Confidence 임계값(MIN_CONF=0.4) 튜닝 및 Low-light Fallback 검증 | `config.py` | ✅ |

> **Phase 2 폴리시 (2026-05-03 적용):**
> - `VisionInterface`에 `conf_threshold` / `set_conf_threshold` 추상 메서드 추가 → `MockVision` 구현 (인터페이스 대칭 확보)
> - `FusionEngine.reset_filter()` 추가; 센서 워커 재초기화 시 이동평균 버퍼 리셋
> - `RASEYES_MOCK=1` 환경에서 임의 CPU 온도(35~45°C) 생성 → CSV 기록
> - `tests/benchmark_vision.py`에 `detector.stop()` 후 모델 해제 검증 assert 및 pytest 케이스 추가
> - `tests/test_fusion.py` 하드코딩 신뢰도 값을 `config.MIN_CONFIDENCE` 기반으로 교체

**Phase 2 완료 기준:** MPS 환경에서 추론 지연 시간 < 60ms, FPS ≥ 15 달성. ✅ **달성**

---

## Phase 3 · 테스트 스위트 구축 ✅
> 목표: 핵심 로직의 회귀(Regression) 방지 및 KPI 자동 검증

| # | 테스트 케이스 | 파일 | 상태 |
|---|---------------|------|------|
| 3-1 | ToF 이동 평균 필터 동작 검증 | `tests/test_sensor.py` | ✅ |
| 3-2 | 거리 임계값 경계 조건 — 100cm·150cm 경계에서 정확히 등급 분류 | `tests/test_fusion.py` | ✅ |
| 3-3 | Low-light Fallback 전환 로직 — Confidence < MIN_CONFIDENCE 시 ToF 단독 모드 진입 | `tests/test_fusion.py` | ✅ |
| 3-4 | Mock 객체를 활용한 오탐지 시나리오 테스트 | `tests/test_false_positive.py` | ✅ |
| 3-5 | 오디오 컨트롤러 — 거리에 따른 비프음 주기 정확성 | `tests/test_audio.py` | ✅ |
| 3-6 | CSV 로거 — 스키마 및 1초 주기 기록 검증 | `tests/test_logger.py` | ✅ |

> **계획 외 추가 완료:**
> | + | MockVision·MockCamera 동작 검증 (라이프사이클, 주입, 해상도) | `tests/test_vision.py` | ✅ |
> | + | FPS Fallback 통합 테스트 (FPS 임계값 초과·복구·ToF 단독 모드 연동) | `tests/test_fps_fallback.py` | ✅ |
> | + | 장기 안정성 테스트 — Mock 모드 5초 연속 실행 시 메모리 누수 없음 검증 (tracemalloc, 4 MiB 기준) | `tests/test_longevity.py` | ✅ |

**Phase 3 완료 기준:** `pytest` 전체 통과 (커버리지 핵심 로직 기준 80% 이상). ✅ **달성** (72 tests passing)

---

## Phase 4 · Orange Pi 5 하드웨어 이식 (On-Device) ✅
> 목표: HAL 구현체만 교체하여 동일 코드베이스를 Orange Pi 5에서 구동

### 4-0. 하드웨어 환경 구성 ✅
> 2026-06-20 완료

| # | 작업 | 결과 |
|---|------|------|
| 4-0-1 | SSH 키 기반 접속 설정 | `ssh raseyes` (192.168.219.145, `~/.ssh/config`) |
| 4-0-2 | OV13855 MIPI CSI 카메라 드라이버 활성화 | overlay `ov13855-c3`, `/dev/video11` (rkisp mainpath) |
| 4-0-3 | VL53L1X ToF 센서 I2C 연결 확인 | overlay `i2c5-m3`, i2c-5 (0x29) |
| 4-0-4 | Python 드라이버 설치 및 동작 검증 | VL53L1X 625mm 측정, 카메라 1280×720 @30FPS |

> **`/boot/orangepiEnv.txt`에 추가된 설정:**
> ```
> overlays=i2c5-m3 ov13855-c3
> ```
>
> **pimoroni `vl53l1x` 0.0.5 — 64비트 aarch64 ctypes 버그 (필수 수정):**
> ```python
> from ctypes import c_void_p, c_int, c_uint, c_uint16
> lib = VL53L1X._TOF_LIBRARY
> lib.initialise.restype                              = c_void_p
> lib.startRanging.argtypes                          = [c_void_p, c_int]
> lib.stopRanging.argtypes                           = [c_void_p]
> lib.getDistance.argtypes                           = [c_void_p]
> lib.getDistance.restype                            = c_uint16
> lib.setMeasurementTimingBudgetMicroSeconds.argtypes = [c_void_p, c_uint]
> lib.setInterMeasurementPeriodMilliSeconds.argtypes  = [c_void_p, c_uint]
> ```
> (수정 없이 `set_timing` / `start_ranging` 호출 시 segfault)

### 4-A. 하드웨어 HAL 구현체 작성 ✅
> 2026-06-21 완료

| # | 작업 | 파일 | 상태 |
|---|------|------|------|
| 4-A-1 | `CSICameraHAL` — OpenCV VideoCapture(`/dev/video11`) MIPI CSI 구현 | `vision/csi_camera_hal.py` | ✅ |
| 4-A-2 | `VL53L1XHAL` — pimoroni vl53l1x (ctypes 패치 포함), i2c-5 기반 ToF 구현 | `sensor/vl53l1x_hal.py` | ✅ |
| 4-A-3 | `JackAudioHAL` — sounddevice + numpy 사인파, 3.5mm 잭 오디오 구현 | `audio/jack_hal.py` | ✅ |

> **참고:** 기존 로드맵의 `USBCameraHAL` → `CSICameraHAL`로 변경 (웹캠 대신 OV13855 MIPI CSI 사용).
> `JackAudioHAL`은 playsound 대신 sounddevice + numpy 사인파 방식으로 구현 (10ms fade 클릭 방지).

### 4-B. YOLOv8 → RKNN 변환 및 NPU 최적화
| # | 작업 | 비고 | 상태 |
|---|------|------|------|
| 4-B-1 | YOLOv8 Nano ONNX → `.rknn` INT8 변환 (PC에서 rknn-toolkit2 사용) | `scripts/export_rknn.py` | ✅ |
| 4-B-2 | Orange Pi 5에서 rknnlite2 추론 속도 측정 및 튜닝 | **평균 27.5ms, 36.3 FPS** (INT8, NPU_CORE_0_1) | ✅ |
| 4-B-3 | CPU 추론 fallback 유지 (rknnlite2 초기화 실패 시 PyTorch CPU) | `vision/rknn_detector.py` | ✅ |

### 4-C. 시스템 서비스 구성
| # | 작업 | 파일 | 상태 |
|---|------|------|------|
| 4-C-1 | `systemd` 서비스 유닛 파일 작성 (전원 인가 시 자동 실행) | `raseyes.service` | ✅ |
| 4-C-2 | 부팅 완료 오디오 큐 구현 (MID→MID→HIGH 멜로디) | `audio/boot_sequence.py` | ✅ |
| 4-C-3 | 물리 버튼(GPIO) 이벤트 핸들러 구현 | `sensor/button_handler.py` | ✅ |
| 4-C-4 | 부팅 시간 < 45초 달성 검증 | kernel 3.8s + userspace 7.2s = **11초** | ✅ |

### 4-D. Orange Pi 5 배포 및 현장 검증 ✅
| # | 작업 | 비고 | 상태 |
|---|------|------|------|
| 4-D-1 | GitHub push → Orange Pi 5 git clone | `ssh raseyes 'git clone ...'` | ✅ |
| 4-D-2 | Orange Pi 5 의존성 설치 | sounddevice, gpiod, VL53L1X, rknnlite2 | ✅ |
| 4-D-3 | 각 HAL 단품 동작 테스트 (CSI 카메라 / ToF / 오디오) | CSICameraHAL PASS · VL53L1XHAL PASS · JackAudioHAL PASS | ✅ |
| 4-D-4 | `RASEYES_HW=1 python main.py` E2E 통합 테스트 | ToF 실측으로 MID/HIGH 경보 정상 동작 확인 | ✅ |
| 4-D-5 | `yolov8n.rknn` 생성 후 scp 전송 | INT8 양자화(COCO128), GitHub Actions → scp | ✅ |
| 4-D-6 | RKNN 추론 속도 측정 (50회 평균) | **평균 27.5ms · P95 29.9ms · 36.3 FPS** ✓ | ✅ |
| 4-D-7 | systemd 서비스 등록 및 부팅 자동 시작 검증 | `enabled`, `active (running)` | ✅ |
| 4-D-8 | 부팅 시간 < 45초 달성 확인 | 11초 달성 | ✅ |

> **4-D 현장에서 발견·수정한 버그:**
> - `VL53L1X._TOF_LIBRARY` 모듈 레벨 변수 접근 오류 수정
> - `i2c_port=` → `i2c_bus=` 파라미터명 수정 (pimoroni v0.0.5)
> - `start_ranging(3)` LONG 모드 → `start_ranging(2)` MEDIUM 모드
>   (LONG 모드 최소 타이밍 버짓 140ms, 50ms 설정 시 측정 주기 ~1s → 데이터 만료)
> - `_build_vision()` factory에서 `.rknn` 파일 존재 확인 → 없으면 MockVision graceful fallback
> - JackAudioHAL: `libportaudio2` apt 설치 없이 시스템 PortAudio 자동 탐지로 정상 동작

**Phase 4 완료 기준:** Orange Pi 5에서 Mock 없이 실제 하드웨어로 동일 파이프라인 동작, 부팅 후 오디오 큐 출력. ✅ **달성**

> **Phase 4 코드 리뷰 핫픽스 (2026-06-26):**
> - `pytest.ini` 추가 (`testpaths = tests`) — `scripts/test_device.py` pytest 수집 에러 근본 해결
> - `sensor/button_handler.py` — `_poll_loop` GPIO 초기화 실패 시 Chip 리소스 누수 수정
> - `sensor/vl53l1x_hal.py` — `start()` 예외 시 open된 I2C connection 누수 수정
> - `main.py` — CSV `write_row()` try-except 보호로 로깅 에러가 메인 루프 크래시 방지

---

## Phase 5 · 시스템 최적화 및 안정화 ✅
> 목표: KPI 전항목 충족 및 엣지 케이스 대응

| # | 작업 | KPI 연결 | 상태 |
|---|------|----------|------|
| 5-1 | E2E Latency 프로파일링 및 병목 제거 | < 500ms | ✅ |
| 5-2 | Graceful Degradation — CPU 온도 80°C 초과 시 FPS 자동 하향 | 열 스로틀링 < 5% | ✅ |
| 5-3 | 카메라 가림 감지 로직 (픽셀 변화량 임계값 감지 → 알림) | 엣지 케이스 | ✅ |
| 5-4 | 배터리 잔량 20% 미만 오디오 경고 구현 | Should Have | ✅ |
| 5-5 | 발열 제어 액티브 쿨러 — 4/6번 핀(5V/GND) 상시 구동 팬 하드웨어로 해결 | Should Have | ✅ |

> **Phase 5 구현 상세 (2026-06-27):**
> - **5-1:** `main.py` 메인 루프에 E2E 레이턴시 EMA 측정 추가. 400ms 초과 시 경고 로그 출력. `logs/logger.py` CSV에 `latency_ms` 컬럼 추가.
> - **5-2:** `threading.Event`(`_thermal_event`) 로 vision worker에 스로틀 신호 전달. 80°C 초과 시 `frame_interval` = 1/5s, vision worker 추론 후 추가 슬립. 복귀 시 자동 원복.
> - **5-3:** 비전 큐에서 수신한 프레임의 프레임 간 평균 픽셀 변화량(numpy)을 계산. 15 프레임 연속 < 3.0 픽셀 시 HIGH 경보 + 5초 쿨다운.
> - **5-4:** `_read_battery_percent()` 함수로 `/sys/class/power_supply/battery/capacity` 읽기. 30초 주기, 20% 미만 시 MID 경보. sysfs 없으면 조용히 skip.
> - **5-5:** 5V 상시 구동 팬을 GPIO 4번(5V)/6번(GND) 핀에 직결. 소프트웨어 제어 불필요.

**Phase 5 완료 기준:** 실외 30분 연속 구동 시 스로틀링 없음, 모든 KPI 수치 충족. ✅ **달성**  
**테스트:** 기존 72개 + 신규 20개 = **92 tests passing** ✅

---

## Phase 6 · PoC 검증 및 베타 테스트 🔄
> 목표: 실 사용자 환경에서의 최종 실증 및 개선점 도출

| # | 작업 | 상태 |
|---|------|------|
| 6-1 | 통제 환경(실내) 하드웨어 착용 테스트 — 비프음 동작 확인 | ✅ |
| 6-2 | CSV 로그 추출 및 FPS 방어율·평균 CPU 온도·알람 빈도 분석 | 🔲 Phase 7 완료 후 진행 |
| 6-3 | 비프음 UX vs TTS 의사결정 | ✅ **→ TTS 결정** |

> **6-3 결론 (2026-07-06):**  
> 비프음만으로는 카메라(YOLO) 탐지 결과인 **"무엇이"·"어디에"** 정보가 사용자에게 전달되지 않음.  
> 단순 거리 기반 비프음은 ToF 센서 배열만으로도 동일한 UX 제공이 가능해 카메라의 부가 가치가 없음.  
> **espeak-ng TTS로 객체 레이블 + 방향(좌/정면/우) + 거리를 음성으로 알리는 방식 채택.**

**Phase 6 완료 기준:** TTS 통합(Phase 7) 이후 실내·외 착용 테스트에서 CSV 로그 분석 완료, UX 개선점 도출.

---

## Phase 7 · TTS 통합 — 카메라 정보 음성 전달 ✅
> 목표: YOLO 탐지 결과(무엇이·어디에)를 espeak-ng TTS로 사용자에게 전달, 카메라 활용 가치 극대화

### 피드백 설계

| 위험도 | 오디오 출력 | 예시 |
|--------|-------------|------|
| HIGH RISK | 비프음 즉각 + TTS | `"위험, 정면 80센티 사람"` |
| MID RISK | TTS 음성만 | `"왼쪽에 의자"` |
| ToF 단독 (HIGH) | 비프음 + TTS | `"위험, 전방 장애물"` |
| ToF 단독 (MID) | TTS 음성만 | `"주의, 장애물"` |
| NONE | 침묵 | — |

> 방향 분류: bbox 중심 x좌표 기준. x < 33% → 왼쪽, x > 66% → 오른쪽, 나머지 → 정면  
> TTS 쿨다운: HIGH 2초, MID 4초 (비프음 쿨다운과 독립적으로 관리)

### 구현 작업

| # | 작업 | 파일 | 상태 |
|---|------|------|------|
| 7-0 | `BaseTtsHAL` 추상 클래스 정의 — `speak(text)` / `stop()` 인터페이스; `_build_tts()` 반환 타입으로 사용 | `audio/tts_hal.py` | ✅ |
| 7-1 | `FusionResult`에 `top_label`, `direction` 필드(`field(default=None)`) 추가; `evaluate()`에서 신뢰도 최고 탐지 객체 선택 및 방향 계산 | `fusion/engine.py` | ✅ |
| 7-2 | `EspeakTts(BaseTtsHAL)` 구현 — espeak-ng 비동기(논블로킹) subprocess, HIGH/MID 쿨다운 독립 관리, 진행 중 프로세스 교체(HIGH 우선) | `audio/tts.py` | ✅ |
| 7-3 | `MockTts(BaseTtsHAL)` 구현 — 콘솔 출력 대체; `last_spoken` 속성으로 테스트 검증 | `audio/mock_tts.py` | ✅ |
| 7-4 | TTS 쿨다운 상수, 방향 분류 비율 상수(`TTS_DIRECTION_LEFT_RATIO=0.33`, `TTS_DIRECTION_RIGHT_RATIO=0.66`), espeak-ng 속도·음성 상수, COCO 한국어 레이블 매핑 추가 | `config.py` | ✅ |
| 7-5 | `_build_tts()` 팩토리 추가(`_build_audio()` 패턴 동일); `self._tts` 필드 추가 및 `stop()` 수명 주기 연결; TTS를 비프음과 병렬 호출; `_mute_active` 플래그 TTS에도 적용; **기존 버그 수정** — `play_occlusion_alert()` 호출에 `_mute_active` 체크 누락 수정 | `main.py` | ✅ |
| 7-6 | 부팅 TTS 연동 — 멜로디 이후 `"라스아이즈 준비 완료"` 발화; `play(audio_hal, tts: Optional[BaseTtsHAL] = None)` 시그니처로 하위 호환 유지 | `audio/boot_sequence.py` | ✅ |
| 7-7 | `audio/__init__.py` — `EspeakTts`, `MockTts`, `BaseTtsHAL` re-export 추가 | `audio/__init__.py` | ✅ |
| 7-8 | `logs/logger.py` CSV에 `tts_spoken` 컬럼 추가 (기본값 `""`, `write_row()` 파라미터 기본값 추가로 기존 호출 하위 호환 유지) | `logs/logger.py` | ✅ |
| 7-9 | 테스트 — 방향 분류 경계(bbox 다양화 전용 helper), TTS 쿨다운, `MockTts.last_spoken` 검증 | `tests/test_tts.py` | ✅ |
| 7-10 | Orange Pi 5 espeak-ng 설치 가이드 — 시스템 패키지(`apt`)이므로 requirements 제외; 주석으로 설치 명령 기록 | `requirements-rpi.txt` | ✅ |

> **주의 사항:**
> - `BaseTtsHAL` 없이 `EspeakTts`/`MockTts`를 직접 구현하면 `_build_tts()` 반환 타입이 `Any`가 되어 타입 안전성이 없어짐 → 7-0 먼저 구현
> - `FusionResult` 필드는 `field(default=None)` 추가이므로 기존 테스트 생성자 호환 유지됨
> - 기존 `test_fusion.py` / `test_false_positive.py`의 `_det()` helper는 `bbox=(0,0,100,100)` 고정 → 중심 x=50 → 항상 "왼쪽". 방향 테스트 전용 helper는 별도로 작성할 것
> - `play_occlusion_alert()` 음소거 미적용은 Phase 5부터 존재하는 버그. Phase 7-5에서 함께 수정
> - `CsvLogger.FIELDNAMES`에 `tts_spoken` 추가 시 `test_phase5.py::TestE2ELatencyCsv::test_latency_ms_in_fieldnames`는 통과하나, `test_logger.py`에서 스키마를 직접 검사하는 테스트가 있으면 업데이트 필요
> - espeak-ng는 Python 패키지가 아닌 시스템 패키지: `sudo apt install espeak-ng espeak-ng-data-ko`
>
> **COCO 한국어 매핑 예시 (자주 등장 클래스):**  
> `person→사람`, `bicycle→자전거`, `car→자동차`, `motorcycle→오토바이`,  
> `chair→의자`, `bench→벤치`, `dining table→테이블`, `door→문`  
> 미매핑 클래스는 영문 레이블 그대로 발화 (fallback).

**Phase 7 완료 기준:** HIGH/MID RISK 발생 시 탐지 객체명 + 방향 + 거리가 음성으로 출력됨. 동일 위험 상황에서 TTS가 쿨다운(HIGH 2초 / MID 4초) 이내 재발화하지 않음. `pytest` 전체 통과. ✅ **달성**  
**테스트:** 기존 92개 + 신규 33개 = **125 tests passing** ✅

---

## 마일스톤 요약

```
Phase 0  ──✅── 기반 구축
Phase 1  ──✅── PC Mock 파이프라인 완성
Phase 2  ──✅── MPS 비전 AI 통합
Phase 3  ──✅── pytest 테스트 스위트  (72 tests passing)
Phase 4  ──✅── Orange Pi 5 하드웨어 이식 완료 (INT8 NPU 27.5ms · 36.3 FPS)
Phase 5  ──✅── 최적화 & 안정화  (92 tests passing)
Phase 6  ──🔄── PoC 베타 테스트 (6-2 CSV 분석은 Phase 7 후 진행)
Phase 7  ──✅── TTS 통합 (espeak-ng, 카메라 정보 음성 전달, 125 tests passing)
```

---

## 기술 스택 참조

| 영역 | PC (개발·검증) | RPi 5 (프로덕션) |
|------|----------------|-----------------|
| Vision | YOLOv8 Nano + PyTorch MPS | YOLOv8 Nano RKNN INT8 (NPU) |
| Camera | OpenCV (파일/웹캠) | OpenCV MIPI CSI (`/dev/video11`, OV13855) |
| ToF | MockToFSensor | VL53L1X I2C5_M3 드라이버 (smbus2) |
| Audio | 콘솔 시뮬레이션 / espeak-ng TTS | ALSA 비프음 (3.5mm 잭) + espeak-ng TTS |
| OS 서비스 | 직접 실행 | systemd 데몬 |
| 테스트 | pytest + Mock | pytest (On-device) |
