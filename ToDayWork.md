# 2026-06-27 오늘의 작업

## Phase 5 · 시스템 최적화 및 안정화 완료

### 5-1 · E2E Latency 프로파일링
- `logs/logger.py`: `FIELDNAMES`에 `latency_ms` 컬럼 추가, `write_row()` 파라미터 추가
- `main.py`: `vision_ts` 기준 EMA 레이턴시 측정 (`_FPS_EMA_ALPHA` 평활화), 400ms 초과 시 경고 로그, CSV 기록

### 5-2 · Thermal Graceful Degradation
- `config.py`: `THERMAL_THROTTLE_TEMP_C = 80.0`, `THERMAL_THROTTLE_FPS = 5` 추가
- `main.py`:
  - `RasEyesApp`에 `self._thermal_event = threading.Event()` 추가
  - `_vision_worker()` 시그니처에 `throttle_event: Optional[threading.Event] = None` 추가 (기존 호출 하위 호환 유지)
  - vision worker: 스로틀 활성화 시 추론 후 `1/THERMAL_THROTTLE_FPS` 슬립
  - 메인 루프: CSV 기록 주기(1초)마다 CPU 온도 확인 → 80°C 초과 시 event.set() + frame_interval 하향, 복귀 시 자동 원복

### 5-3 · 카메라 가림 감지
- `config.py`: `CAMERA_OCCLUSION_CHANGE_THRESH = 3.0`, `CAMERA_OCCLUSION_FRAMES = 15`, `CAMERA_OCCLUSION_COOLDOWN_SEC = 5.0` 추가
- `main.py`:
  - `import numpy as np` 추가
  - 비전 큐에서 프레임 변수 `last_frame` 으로 명시 수신 (기존 `_` → `last_frame`)
  - 연속 프레임 간 `np.mean(|curr - prev|)` 으로 픽셀 변화량 측정
  - 15 프레임 연속 변화량 < 3.0 시 HIGH 경보 + 5초 쿨다운

### 5-4 · 배터리 잔량 경고
- `config.py`: `BATTERY_LOW_THRESHOLD_PCT = 20`, `BATTERY_CHECK_INTERVAL_SEC = 30.0`, `BATTERY_SYSFS_PATH` 추가
- `main.py`: `_read_battery_percent()` 함수 신규 추가 (`_read_cpu_temp()`와 동일 패턴), 30초 주기 확인, 20% 미만 시 MID 경보

### 5-5 · 액티브 쿨러 PWM 제어
- `scripts/pwm_fan_control.py` 신규 생성
  - `/sys/class/pwm/pwmchip0/pwm0/` sysfs 제어
  - 온도→듀티 선형 보간: <50°C→20%, 50~70°C→20~80%, ≥80°C→100%
  - 5초 주기 루프, SIGTERM/SIGINT graceful exit

### 테스트
- `tests/test_phase5.py` 신규 생성 (20개 테스트)
- **최종 결과: 92 tests passing** (기존 72 + 신규 20)

---

## Phase 5 코드 리뷰 피드백 반영 (feedback.txt)

### [#1] E2E 레이턴시 중복 누적 버그 수정 (`main.py`)
- E2E 레이턴시 계산 블록 직후 `current_vision_ts = None` 리셋 추가
- 신규 비전 프레임을 수신했을 때만 EMA에 갱신되도록 수정

### [#2] RKNN 클래스 라벨 불일치 수정 (`vision/rknn_detector.py`)
- COCO 80개 클래스 이름 테이블 `_COCO_CLASSES` 추가
- `label=str(class_ids[i])` → `label=_COCO_CLASSES[int(class_ids[i])]` 로 변경
- CPU YoloDetector와 동일하게 문자열 클래스명 반환

### [#3] 발열 스로틀링 히스테리시스 추가 (`config.py`, `main.py`)
- `config.py`에 `THERMAL_RECOVERY_TEMP_C = 75.0` 추가
- 진입: 80°C 초과 시 스로틀, 복구: 75°C 이하 시 해제 (5°C 마진)
- 80°C 경계에서의 채터링/헌팅 현상 방지

### [#4] ButtonHandler 연동 (`main.py`)
- `RasEyesApp.__init__`에 `_button_handler`, `_mute_active` 필드 추가
- `_toggle_mute()` 콜백 추가 (버튼 누름 → 오디오 음소거 토글)
- `start()`: `use_hw` 모드에서 ButtonHandler 초기화, gpiod 미설치 시 graceful 경고
- `stop()`: ButtonHandler 정지 포함

### [#5] PWM 초기화 순서 수정 (`scripts/pwm_fan_control.py`)
- `period` 쓰기 전 `duty_cycle = 0` 먼저 기록하여 EINVAL(duty_cycle > period) 방지

### [#6] SIGTERM Graceful Shutdown 추가 (`main.py`)
- `signal` 모듈 import 추가
- `run()` 내 SIGTERM 핸들러 등록: 수신 시 `_stop_event.set()`
- `while True:` → `while not self._stop_event.is_set():` 로 변경하여 `finally` 블록 보장

### [#7] 오디오 출력 경합 방지 (`audio/beep_controller.py`, `main.py`)
- `BeepController`에 `_pending_system_alert`, `request_system_alert()`, `pop_system_alert()` 추가
- 배터리 경고: 직접 `play_alert()` 호출 → `beep.request_system_alert(RiskLevel.MID)` 로 변경
- 메인 루프에서 시스템 경고와 퓨전 결과를 병합 후 단일 경로로 재생 (경합 제거)
- `_mute_active` 플래그 체크 추가로 음소거 상태 존중

### [#8] NMSBoxes 반환 타입 대응 (`vision/rknn_detector.py`)
- `indices.flatten()` → `np.array(indices).flatten()` 로 변경
- list/tuple 반환 환경에서도 AttributeError 없이 동작

### 테스트 결과
- **92 tests passing** (기존 92개 모두 통과, 회귀 없음)
