# CLAUDE.md — RasEyes Project

## 1. Project Overview
* **RasEyes:** 시각장애인용 상단 사각지대(가슴~머리 높이) 장애물 탐지 웨어러블 엣지 AI 디바이스.
* **Target HW:** Orange Pi 5 (4GB, RK3588S+NPU), USB 웹캠(미구매), VL53L1X(ToF), 3.5mm 이어폰 잭 출력.
* **Current Phase:** Orange Pi 5 배포 단계. PC(Linux, Python 3.13)에서 개발하고 OPi5로 배포(`raseyes.service`).
* **Constraints:** 100% On-device, 외부 API/Cloud 사용 불가.
* **KPIs:** End-to-End Latency < 500ms, 추론 < 60ms(15+ FPS), 탐지 Recall > 95%, 오탐지 < 1회/분.
* **문서:** `docs/PRD.md`(요구사항), `docs/TRD.md`(기술 명세), `docs/1.0_ROADMAP.md`(v1.0 완료 기록), `docs/2.0_ROADMAP.md`(진행 중 로드맵), `docs/ToPost.md`(최신 작업 일지), `docs/checklist.md`(착용 테스트 체크리스트), `docs/equipment.txt`(장비 목록), `docs/wantToMake.md`(구현 아이디어 초안).

## 2. Project Structure & Rules
* `/vision`, `/sensor`, `/fusion`, `/audio`, `/logs`, `/scripts` 등 도메인별 폴더 분리. `main.py`는 오케스트레이션만 담당.
* `logs/logger.py`: CSV 로깅 전담(세션마다 별도 파일). `logs/clip_recorder.py`: HIGH 경보 전후 프레임을 JPEG 시퀀스로 저장(`logs/events/`). `fusion/alert_policy.py`: 위험 '상태'를 경보 '이벤트'로 변환(엣지 트리거+히스테리시스). `scripts/`: RKNN 모델 변환/벤치마크·로그 수집/분석·센서 실측 유틸.
* 입력(비전, 센서)과 출력(오디오)은 반드시 추상화 계층(HAL) 인터페이스를 적용하여, 현재의 PC 모킹 클래스와 추후 Orange Pi 5 하드웨어 제어 클래스를 쉽게 교체할 수 있도록 구현.
* 상수 및 임계값은 매직 넘버 대신 `config.py`에 분리.
* 타입 힌트와 구글 스타일 Docstring 필수 작성. 예외 처리 철저.

## 3. Core Logic (Sensor Fusion)
* **High Risk:** 객체 인식됨 & ToF 거리 <= 100cm & Confidence >= MIN_CONF. (즉각 경고 트리거)
* **Mid Risk:** 객체 인식됨 & ToF 거리 <= 150cm. (주의 경고 트리거)
* **Low-light Fallback:** 비전 모델의 Confidence가 0.4 미만이면 ToF 단독 모드로 전환.
* **FPS Fallback은 `main._should_fps_fallback()`이 판정한다.** 저전력(4 FPS)·발열 스로틀링(5 FPS)은 둘 다 `FPS_FALLBACK_THRESHOLD`(8)보다 낮아, 제외하지 않으면 **의도적으로 FPS를 낮추는 순간 비전이 꺼져 모드가 스스로를 무력화한다** (2026-07-28 Pi 실측: 저전력 진입 0.7초 뒤 ToF 단독 전환). Fallback은 카메라 멈춤·과부하 같은 예기치 못한 붕괴만 잡는다. 저전력 구간의 진짜 멈춤은 비전 워커 Watchdog(`_check_vision_stall`)이 담당한다.
* ToF 센서 값은 노이즈 제거를 위해 이동 평균 필터(window=3) 적용.
* **경보 발화는 `fusion/alert_policy.py`가 게이트한다.** 위험 판정(`RiskLevel`)은 "거리 <= 임계값"인 **상태**라 그대로 흘리면 매 사이클 경보가 나간다 (2026-07-28 야외 실측: HIGH 상태 55.8%, 비프 분당 181회). `AlertPolicy`는 위험 수준이 **올라가는 순간**에만 통과시키고, 지속 중에는 `ALERT_REMINDER_SEC` 간격 리마인더만 허용한다. 해제는 `임계값 + ALERT_HYSTERESIS_CM`을 넘어야 이뤄져 임계값 근처 진동을 흡수한다.
  * 음소거 해제(`_toggle_mute`)와 ToF 센서 재초기화(`_on_sensor_reinit`) 시 반드시 `AlertPolicy.reset()`을 호출한다. 래치가 남으면 실제 위험을 놓친다.
  * 배터리 등 **시스템 경고는 정책을 우회**한다 (장애물 경보가 아니므로).
  * OoR(`TOF_OUT_OF_RANGE_CM`)은 히스테리시스와 무관하게 래치를 해제한다 — 측정 상한이 해제선보다 짧은 레인징 모드에서 영원히 안 풀리는 것을 막는 백스톱.

## 4. Development & Testing
* 비전 AI 환경: PC는 Linux x86_64(CPU)로 개발 — GPU 가속 불필요. 실제 추론은 Orange Pi 5 NPU(RKNN)에서 수행되므로 PC에서는 정확도/로직 검증 목적으로만 YOLO를 CPU로 돌린다.
* 테스트: `pytest` 프레임워크 사용. (핵심 케이스: 거리 임계값 경계 조건, Fallback 전환 로직, 모킹 객체를 활용한 오탐지 테스트).
* 로깅: 로컬 CSV 파일에 1초 1회 기록 (timestamp, cpu_temp, fps, tof_distance_cm, alert_triggered, latency_ms, tts_spoken, occlusion_alerts, alerts_emitted, tof_raw_cm, tof_only_ratio).
  * **CSV는 세션(프로세스 실행)마다 새 파일**(`logs/raseyes_log_<타임스탬프>.csv`)에 쓴다. 단일 경로에 `mode="w"`로 쓰던 방식은 재시작마다 직전 세션을 덮어써 2026-07-28 야외 테스트 로그를 통째로 날렸다. Pi에 RTC 배터리가 없어 부팅 시 시계가 되감기므로, 파일명이 충돌하면 일련번호를 붙여 **절대 덮어쓰지 않는다**.
  * 뒤 3개 컬럼은 진단용이다. `alerts_emitted`(실제 발화 횟수)만 보면 ToF가 통째로 OoR이 되어 조용해진 경우를 개선으로 오판하므로, `tof_raw_cm`(OoR 비율 산출)과 `tof_only_ratio`(비전 실명 정도)를 함께 본다. `analyze_logs.py`가 OoR 70% 초과 시 "센서 실명 의심"을 표시한다.
* 시간 의존 로직(쿨다운·샘플링 주기·보존 기간)은 `time.monotonic()`을 내부에서 호출하지 말고 **`now: float`를 인자로 받는다** (`logs/clip_recorder.py` 참고). 메인 루프가 이미 계산한 값을 재사용하고, 테스트에서 `sleep` 없이 시간을 조작할 수 있다.

## 5. Commands

| 명령어 | 설명 |
|--------|------|
| `pip install -r requirements.txt` | 의존성 설치 (PC/dev) |
| `pip install -r requirements-rpi.txt` | Orange Pi 5 전용 의존성 (배포 시 Pi에서 설치, PC에서는 미사용) |
| `python main.py` | 기본 실행 (카메라·모델 필요) |
| `RASEYES_MOCK=1 python main.py` | Mock 모드 실행 (카메라·모델 불필요) |
| `RASEYES_HW=1 python main.py` | Orange Pi 5 HW HAL 사용 |
| `pytest` | 전체 테스트 실행 |
| `pytest tests/test_fusion.py` | 퓨전 로직 단위 테스트 |
| `bash scripts/pull_logs.sh` | Pi의 운영 CSV·이벤트 클립을 `logs_archive/`로 수집 (PC에서 실행) |
| `python scripts/analyze_logs.py logs_archive/*.csv` | 수집한 CSV의 FPS·온도·경보 빈도 KPI 분석 |
| `python3 scripts/tof_range_bench.py --mode short --seconds 60` | VL53L1X 레인징 모드 실측 (**Pi에서 실행**). SHORT 전환 가부 판단용 — 범위 초과 시 반환값·유효 최대 거리·노이즈 측정 |

## 6. Environment Variables
* `RASEYES_MOCK=1`: 모든 컴포넌트를 Mock으로 교체 (카메라·모델 불필요). 개발 기본값.
* `RASEYES_HW=1`: Orange Pi 5 HW HAL 사용. 초기화 실패 시 자동 fallback.

## 7. Audio Threading Rules
* **오디오 재생:** `audio/resident_stream.py`의 `ResidentAudioStream` (상주 `sd.OutputStream` + 콜백) 사용 필수. 재생마다 ALSA 디바이스를 열고 닫는 방식(구 `sd.play()/sd.stop()` 패턴) 금지 — 코덱/앰프 반복 온오프로 인한 전류 스파이크(보조배터리 OCP 트립 위험)를 방지하기 위함.
  * 각 오디오 출력 클래스(`JackAudioHAL`, `PiperTts`, `EspeakTts`)는 생성 시점(`JackAudioHAL`은 `start()`, TTS는 `__init__`)에 자신의 `ResidentAudioStream` 인스턴스 하나를 열고 프로세스 종료(`stop()`)까지 유지한다.
  * 재생: `self._stream.play(stereo, interrupt=False)` — 버퍼에 채워 넣기만 하므로 별도 스레드 없이도 논블로킹. `interrupt=True`는 현재 재생/대기 중인 오디오를 즉시 버리고 교체(HIGH 우선순위 선점용).
  * 선점(중단): `self._stream.clear()`로 재생 버퍼를 즉시 비운다. `sd.stop()`은 더 이상 사용하지 않는다.
  * `is_speaking()` / 발화 상태 확인: `(합성 스레드 is_alive()) or self._stream.is_playing()` 형태로 판단한다 (합성과 재생을 모두 커버).
* **합성(synthesis) 스레드:** TTS는 신경망/subprocess 추론이 CPU를 점유하므로 여전히 백그라운드 스레드에서 수행한다. 이 스레드는 합성만 담당하고, 완료 후 `self._stream.play()`로 넘기고 곧바로 종료한다 (재생 완료를 기다리는 폴링 루프 없음).
* **스레드 정지 플래그:** `_stop_flag.clear()` 사용 금지. 선점 시 `self._stop_flag = threading.Event()` 로 새 인스턴스 교체 (레이스 컨디션 방지).
* **TTS 스택:** PiperTts (모델: `models/tts/ko_KR-kss-medium.onnx`) → EspeakTts → MockTts 우선순위. 모델 설치: `bash scripts/download_piper_model.sh`.
* **미설치 라이브러리 테스트:** PC에 `sounddevice`, `piper` 미설치. `ResidentAudioStream.start()`는 `sounddevice` 미설치/디바이스 오류 시 예외를 삼키고 재생을 비활성화(no-op)하므로, 재생 로직 테스트는 `patch.object(tts, "_stream")`으로 스트림 자체를 모킹하고, `ResidentAudioStream` 자체의 단위 테스트는 `patch.dict(sys.modules, {"sounddevice": MagicMock()})` 사용.
* **외부 모델 초기화:** 모델 파일이 필요한 클래스 생성 전 반드시 `os.path.exists(path)` 선행 확인 후 fallback 처리.

## 8. Orange Pi 5 배포 (Deployment)
* Pi(`ssh raseyes`)는 **git이 아니라 rsync로 배포**한다. Pi의 git 이력은 실제 배포 상태와 무관하게 뒤처져 있으므로 `git pull`은 사용하지 않는다.
* 배포 시 제외: `.git/`, `.venv/`, `models/`(대용량 바이너리, Pi에 이미 존재), `logs/*.csv`(운영 로그), `logs/events/`(경고 이벤트 클립), `*.md`(Pi에서 별도로 편집된 작업 노트가 있어 덮어쓰면 유실됨), `logs_archive/`(PC 전용 수집 아카이브), `.deploy_backup/`(Pi의 이전 배포 백업 — 롤백 수단), `__pycache__/`(PC는 Python 3.13, Pi는 3.10이라 pyc가 서로 무효), `.pytest_cache/`. 배포 전 `rsync -n`(dry-run)으로 변경/삭제 목록을 반드시 확인.
* ⚠️ **`logs/events/` 제외를 빠뜨리면 안 된다.** `logs/*.csv` 패턴은 하위 **디렉터리**를 걸러내지 못하므로, PC에 없는 `logs/events/`가 `rsync --delete`의 삭제 대상이 되어 **Pi에 쌓인 이벤트 클립이 배포 한 번에 전멸한다** (Phase 3 자체가 무의미해짐).
* `raseyes.service`는 `.venv`가 아니라 `/usr/bin/python3`로 직접 실행된다 — 의존성은 시스템 전역 `pip3`에 설치되어 있어야 한다.
* `sudo systemctl restart/status raseyes.service`는 대화형 비밀번호가 필요해 Claude가 직접 실행할 수 없다 (보안 정책상 커맨드에 평문 비밀번호를 넣는 것은 자동 차단됨) — 사용자가 `! ssh -t raseyes "sudo systemctl restart raseyes.service"` 형태로 직접 실행해야 한다. `journalctl -u raseyes.service`는 sudo 없이 조회 가능.
* **원격 `sudo`에는 반드시 `ssh -t`를 쓴다.** `-t`가 없으면 TTY가 없어 sudo가 비밀번호 프롬프트를 띄우지 못하고 그대로 멈추거나 `sudo: a terminal is required to read the password`로 실패한다 — 접속 장애로 오인하기 쉽다.
* 안전 종료: `ssh -t raseyes "sudo shutdown -h now"` 실행 후 보드 LED가 꺼질 때까지 기다린 뒤 전원을 분리한다 (강제 차단 시 SD/eMMC 손상 위험).
* Pi 계정명은 **`orangepi`** 다. `orangepi5`는 tailscale 호스트명이므로 사용자명으로 쓰면 `invalid user`로 거부된다. 새 기기(Mac 등)에서 접속하려면 그 기기의 공개키를 Pi의 `~/.ssh/authorized_keys`에 먼저 등록해야 한다 (현재 등록된 키는 리눅스 개발 PC의 `raseyes-dev` 1개뿐).
* 카메라가 고정 거치된 채 정적인 장면을 계속 볼 때 가림 감지(`CAMERA_OCCLUSION_*`)가 오탐하는 것은 알려진 설계 한계이지 버그가 아니다. 실제 착용 시나리오(움직임 있음)에서는 덜 발생할 것으로 예상.