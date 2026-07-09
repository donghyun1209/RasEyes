# 2026-07-09 작업 내용

## 1. feedback.txt 코드 리뷰 반영

### 배경
외부 코드 리뷰(`feedback.txt`)로 지적된 항목을 Explore 서브에이전트 3개로 실제 코드와 대조 검증한 뒤 수정. 이미 해결된 항목(`MockTts.stop`, `main.py`의 6개 함수 docstring)은 재작업하지 않고 스킵.

### 수정 파일
| 파일 | 내용 |
|------|------|
| `audio/tts.py` | `EspeakTts` 선점 레이스 컨디션 수정 — `stop_flag`를 로컬 변수로 캡처해 워커 스레드에 인자로 전달 (`piper_tts.py`와 동일 패턴) |
| `vision/mock_camera.py` | 로드된 이미지를 요청 해상도로 `cv2.resize` (실제 카메라 HAL과 동작 일치) |
| `fusion/engine.py` | `evaluate()`의 중복 조건(`and max_conf >= effective_min_conf`) 제거 |
| `config.py`, `sensor/vl53l1x_hal.py`, `vision/csi_camera_hal.py`, `vision/opencv_camera.py` | 하드웨어 매직넘버를 config 상수로 이전 (`TOF_RANGING_MODE_MEDIUM`, `TOF_STALE_TIMEOUT_SEC`, `CAMERA_BUFFER_SIZE`) |
| `vision/mock.py`, `sensor/mock.py`, `vision/mock_camera.py`, `audio/mock.py`, `audio/beep_controller.py` | 누락된 Google 스타일 docstring 보완 |
| `tests/test_tts.py`, `tests/test_phase5.py` | 누락된 타입 힌트 추가 |

### 의도적으로 스킵 (Simplicity First)
- HAL 파일명(`interface.py`/`hal.py`/`mock.py`) 강제 통일 — 현재 구체적 파일명(`csi_camera_hal.py` 등)이 더 명확
- 순수 수학 변환 상수(`/10.0` mm→cm, `/32768.0` int16 정규화) config.py 이전 — 튜닝 대상 아닌 고정값
- `EspeakTts`의 `np.repeat` 리샘플링을 scipy로 교체 — 새 의존성 부담 대비 실사용 문제 미확인

검증: `pytest` 150개 전체 통과, `RASEYES_MOCK=1 python main.py` 정상 기동 확인.

## 2. Orange Pi 5 배포

### 배경
로컬 작업 트리(HAL 리팩터, `ResidentAudioStream` 도입, prerendered TTS 캐시 등 대규모 미커밋 변경 포함)를 Orange Pi 5에 배포. Pi가 git 기반이 아니라 애드혹 파일 복사로 운영되어 왔고(git 4커밋 지연 + untracked 잔재 파일 다수) 실행 중인 서비스가 있다는 것을 확인, rsync 기반의 안전한 배포 절차로 진행.

### 절차
1. Pi 전체 디렉터리 tar 백업 → `.deploy_backup/20260709_102731/`
2. `rsync -avz --delete`로 로컬 작업 트리 동기화 (`.git`/`.venv`/`models`/`logs/*.csv`/`*.md` 등 제외 — Pi에서 별도 편집된 문서 보호)
3. 잘못된 위치의 잔재 파일 정리 (`main.py.bak`, 루트의 `jack_hal.py`/`download_piper_model.sh` 등)
4. `models/tts/prerendered/` 신규 캐시 별도 동기화
5. `python3 -c 'import main'`으로 import 정상 여부 확인 후 `raseyes.service` 재시작

### 배포 후 확인
- 카메라(CSI)/ToF(VL53L1X)/NPU(RKNN)/오디오 파이프라인 전부 정상 기동, 사람/물체 인식 정상 동작
- 카메라 가림 경고가 반복적으로 울림 → 조사 결과 고정 거치 상태에서 정적인 장면을 계속 봐서 발생하는 알려진 설계 한계로 확인 (배포 회귀 아님, 사용자 요청으로 임계값 조정은 보류)
- 재시작 직후 오디오 믹서 검증 실패/언더런 경고 → 기동 과도 현상으로 확인, 재발 없음
- **보조배터리 단독 구동 테스트 통과**

## 3. Git 커밋 & CLAUDE.md 정비

- 커밋 `912de18`: "HAL 인터페이스 통일, 상주 오디오 스트림 도입, feedback.txt 리뷰 반영" (40개 파일, push는 미실행)
- `CLAUDE.md`에 "8. Orange Pi 5 배포" 섹션 신설: rsync 배포 절차, `raseyes.service`가 시스템 `/usr/bin/python3`로 직접 실행된다는 점, `sudo` 재시작은 대화형 비밀번호 때문에 Claude가 직접 실행 불가하다는 제약, 안전 종료 명령어
- `/claude-md-improver`로 품질 감사(78/100) 후 3건 수정:
  - §4 "Apple Silicon MPS 가속" → 실제 dev 머신(Linux x86_64)과 불일치하던 오류 정정
  - Commands 표에 `requirements-rpi.txt` 설치 명령 추가
  - `PRD.md`/`TRD.md`/`ROADMAP.md`/`ToDayWork.md`/`checklist.md` 문서 링크를 §1에 추가
