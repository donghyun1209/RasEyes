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

## Phase 2 · 비전 AI 통합 (PC, MPS 가속)
> 목표: Apple Silicon MPS에서 YOLOv8 Nano 추론 속도 ≥ 15 FPS 확인

| # | 작업 | 파일 |
|---|------|------|
| 2-1 | YOLOv8 Nano 모델 다운로드 및 `torch.device("mps")` 설정 | `vision/detector.py` |
| 2-2 | `CameraHAL`을 구현하는 실제 OpenCV 캡처 클래스 | `vision/opencv_camera.py` |
| 2-3 | 추론 결과 → BBox + Confidence Score → 퓨전 엔진 연동 | `vision/detector.py` |
| 2-4 | FPS 벤치마크 스크립트 작성 및 추론 지연 시간 측정 | `tests/benchmark_vision.py` |
| 2-5 | Confidence 임계값(MIN_CONF=0.4) 튜닝 및 Low-light Fallback 검증 | `config.py` |

**Phase 2 완료 기준:** MPS 환경에서 추론 지연 시간 < 60ms, FPS ≥ 15 달성.

---

## Phase 3 · 테스트 스위트 구축
> 목표: 핵심 로직의 회귀(Regression) 방지 및 KPI 자동 검증

| # | 테스트 케이스 | 파일 |
|---|---------------|------|
| 3-1 | ToF 이동 평균 필터 동작 검증 | `tests/test_filters.py` |
| 3-2 | 거리 임계값 경계 조건 — 100cm·150cm 경계에서 정확히 등급 분류 | `tests/test_fusion.py` |
| 3-3 | Low-light Fallback 전환 로직 — Confidence < 0.4 시 ToF 단독 모드 진입 | `tests/test_fusion.py` |
| 3-4 | Mock 객체를 활용한 오탐지 시나리오 테스트 | `tests/test_false_positive.py` |
| 3-5 | 오디오 컨트롤러 — 거리에 따른 비프음 주기 정확성 | `tests/test_audio.py` |
| 3-6 | CSV 로거 — 스키마 및 1초 주기 기록 검증 | `tests/test_logger.py` |

**Phase 3 완료 기준:** `pytest` 전체 통과 (커버리지 핵심 로직 기준 80% 이상).

---

## Phase 4 · RPi 5 하드웨어 이식 (On-Device)
> 목표: HAL 구현체만 교체하여 동일 코드베이스를 RPi에서 구동

### 4-A. 하드웨어 HAL 구현체 작성
| # | 작업 | 파일 |
|---|------|------|
| 4-A-1 | `PiCamera3HAL` — `picamera2` 라이브러리 기반 구현 | `vision/picamera_hal.py` |
| 4-A-2 | `VL53L1XHAL` — I2C 드라이버 기반 ToF 구현 | `sensor/vl53l1x_hal.py` |
| 4-A-3 | `BluetoothAudioHAL` — BlueZ + PipeWire 기반 오디오 구현 | `audio/bluetooth_hal.py` |
| 4-A-4 | `BuzzerHAL` — GPIO 비상 부저 구현 (BT 연결 끊김 fallback) | `audio/buzzer_hal.py` |

### 4-B. YOLOv8 → TFLite 변환 및 최적화
| # | 작업 | 비고 |
|---|------|------|
| 4-B-1 | YOLOv8 Nano → TFLite INT8 양자화(Quantization) 변환 | `scripts/export_tflite.py` |
| 4-B-2 | RPi 5에서 TFLite 추론 속도 측정 및 튜닝 | 목표: < 60ms |

### 4-C. 시스템 서비스 구성
| # | 작업 | 파일 |
|---|------|------|
| 4-C-1 | `systemd` 서비스 유닛 파일 작성 (전원 인가 시 자동 실행) | `raseyes.service` |
| 4-C-2 | 부팅 완료 → "RasEyes가 준비되었습니다" 오디오 큐 구현 | `audio/boot_sequence.py` |
| 4-C-3 | 물리 버튼(GPIO) 이벤트 핸들러 구현 | `sensor/button_handler.py` |
| 4-C-4 | 부팅 시간 < 45초 달성 검증 | - |

**Phase 4 완료 기준:** RPi에서 Mock 없이 실제 하드웨어로 동일 파이프라인 동작, 부팅 후 오디오 큐 출력.

---

## Phase 5 · 시스템 최적화 및 안정화
> 목표: KPI 전항목 충족 및 엣지 케이스 대응

| # | 작업 | KPI 연결 |
|---|------|----------|
| 5-1 | E2E Latency 프로파일링 및 병목 제거 | < 500ms |
| 5-2 | Graceful Degradation — CPU 온도 80°C 초과 시 FPS 자동 하향 | 열 스로틀링 < 5% |
| 5-3 | 블루투스 Watchdog 스크립트 (연결 끊김 자동 재연결) | 안정성 |
| 5-4 | 카메라 가림 감지 로직 (픽셀 변화량 임계값 감지 → 알림) | 엣지 케이스 |
| 5-5 | 배터리 잔량 20% 미만 오디오 경고 구현 | Should Have |
| 5-6 | 발열 제어 액티브 쿨러 PWM 제어 스크립트 | Should Have |

**Phase 5 완료 기준:** 실외 30분 연속 구동 시 스로틀링 없음, 모든 KPI 수치 충족.

---

## Phase 6 · PoC 검증 및 베타 테스트
> 목표: 실 사용자 환경에서의 최종 실증 및 개선점 도출

| # | 작업 |
|---|------|
| 6-1 | 통제 환경(공원, 실내 복도) 내 하드웨어 착용 테스트 |
| 6-2 | CSV 로그 추출 및 FPS 방어율·평균 CPU 온도·알람 빈도 분석 |
| 6-3 | 시각장애인 협력자 1~2명 대상 오디오 피드백 직관성 인터뷰 |
| 6-4 | 인터뷰 결과 기반 비프음 UX vs TTS 의사결정 |
| 6-5 | 최종 PoC 결과 보고서 작성 |

---

## 마일스톤 요약

```
Phase 0  ──✅── 기반 구축
Phase 1  ──✅── PC Mock 파이프라인 완성
Phase 2  ──🔲── MPS 비전 AI 통합
Phase 3  ──🔲── pytest 테스트 스위트
Phase 4  ──🔲── RPi 5 하드웨어 이식
Phase 5  ──🔲── 최적화 & 안정화
Phase 6  ──🔲── PoC 베타 테스트
```

---

## 기술 스택 참조

| 영역 | PC (개발·검증) | RPi 5 (프로덕션) |
|------|----------------|-----------------|
| Vision | YOLOv8 Nano + PyTorch MPS | YOLOv8 Nano TFLite INT8 |
| Camera | OpenCV (파일/웹캠) | picamera2 (Camera Module 3) |
| ToF | MockToFSensor | VL53L1X I2C 드라이버 |
| Audio | 콘솔 시뮬레이션 | BlueZ / PipeWire + GPIO Buzzer |
| OS 서비스 | 직접 실행 | systemd 데몬 |
| 테스트 | pytest + Mock | pytest (On-device) |
