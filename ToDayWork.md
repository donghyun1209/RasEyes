# 2026-06-26 작업 기록

## 완료 항목

### Phase 4-D-5 · RKNN 모델 생성 및 전송 ✅

- `tests/benchmark_rknn.py` 신규 작성 — RknnDetector 전용 NPU 벤치마크 (50회 측정, KPI 자동 판정)
- GitHub Actions `build_rknn.yml` 워크플로우 3차 디버깅·수정
  - `quantization_algorithm` → `quantized_algorithm` (rknn-toolkit2 v2.x API 변경)
  - `onnx>=1.16.1` → `onnx>=1.14.0,<=1.16.0` (`onnx.mapping` 1.17+에서 제거됨)
  - urllib 308 redirect 미처리 → `wget`으로 교체
  - INT8 캘리브레이션용 COCO128 데이터셋 자동 다운로드 스텝 추가
- INT8 양자화 모델(`yolov8n.rknn`, 4.8MB) 빌드 완료 → `scp raseyes:~/RasEyes/` 전송

### Phase 4-D-6 · RKNN 추론 속도 측정 ✅

- `vision/rknn_detector.py` 버그 2건 수정
  - 입력 차원 오류: `(H,W,C)` → `np.expand_dims` → `(1,H,W,C)`
  - 듀얼 NPU 코어 적용: `init_runtime(core_mask=RKNNLite.NPU_CORE_0_1)`
- **벤치마크 결과 (서비스 중단, 50회 평균)**

| 지표 | 수치 | KPI |
|------|------|-----|
| 평균 지연 | **27.5 ms** | < 60ms ✅ |
| 최소 지연 | 25.7 ms | - |
| 최대 지연 | 30.0 ms | - |
| P95 지연 | 29.9 ms | - |
| FPS | **36.3** | ≥ 15 ✅ |

- FP16 모델(56.7ms, 17.6 FPS) 대비 INT8이 **2.1배 빠름**
- 주의: systemd `raseyes.service` 실행 중에는 NPU 경합으로 77ms까지 상승 → 실 운영 시 단일 프로세스만 NPU 점유하므로 문제 없음

### ROADMAP 업데이트 ✅

- Phase 4 전 항목 ✅ 완료 처리
- 마일스톤 요약 `🔄 → ✅` 갱신

### 에이전트(Antigravity) 코드 리뷰 및 핫픽스 ✅

- **NPU 리소스 누수 및 예외 처리 보완 (`vision/rknn_detector.py`):**
  - `start()` 중 예외 발생 시 NPU(`self._rknn`)가 해제되지 않고 잔존하던 리소스 누수 수정.
  - `stop()`에 개별 `try-except` 예외 처리를 적용하여 NPU 및 카메라 자원의 강제 해제 보장.
- **pytest 수집 경고 제거 (`scripts/test_device.py`):**
  - `TestResult` 클래스가 pytest 테스트 대상 클래스로 오인되어 경고를 유발하던 현상 해결 (`__test__ = False` 추가).
- **벤치마크 예외 안전성 개선 (`tests/benchmark_rknn.py`):**
  - 측정 중 중단이 발생하더라도 NPU/카메라 리소스가 확실히 정리되도록 `try...finally` 적용.
- **에이전트 역할 규칙 업데이트 (`.agents/AGENTS.md`):**
  - 에이전트 역할을 프로젝트 코드를 직접 수정하지 않고 `feedback.txt`를 통해 코드 리뷰만 수행하는 '코드 리뷰어(Code Reviewer)'로 역할 한정 및 지침 재정의.

### 코드 리뷰 피드백 핫픽스 (feedback.txt Phase 1-4) ✅

- **pytest 수집 에러 근본 해결 (`pytest.ini` 신규 추가):**
  - `pytest.ini` 파일 생성 — `testpaths = tests` 설정으로 pytest가 `tests/` 디렉터리만 탐색하도록 제한.
  - `scripts/test_device.py`가 pytest 수집 단계에서 완전히 배제됨.
- **ButtonHandler Chip 리소스 누수 수정 (`sensor/button_handler.py`):**
  - `_poll_loop` 재구조화: `chip = None`, `line = None` 초기화 후 중첩 `try`로 감쌈.
  - 초기화 중 예외 발생 시 이미 열린 `chip`을 명시적으로 닫고 리턴.
  - `finally` 블록에서 `line`, `chip` 각각 None 체크 후 안전 해제.
- **VL53L1X 초기화 실패 시 리소스 누수 수정 (`sensor/vl53l1x_hal.py`):**
  - `start()` 예외 블록에서 `self._tof`가 이미 open된 경우 `close()` 호출 후 None 처리.
- **메인 루프 CSV 로깅 예외 안전성 보완 (`main.py`):**
  - `write_row()` 호출을 `try-except`로 감싸 디스크 에러가 메인 루프를 다운시키지 않도록 처리.
- **pytest 72개 전체 통과 확인 ✅**

---

## 다음 작업 (Phase 5)

- E2E Latency 프로파일링 (비전+센서+오디오 전체 파이프라인 500ms 이내 검증)
- Orange Pi 5 실외 30분 연속 구동 안정성 테스트
