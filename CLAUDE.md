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
* `logs/logger.py`: CSV 로깅 전담(세션마다 별도 파일). `logs/clip_recorder.py`: HIGH 경보 전후 프레임을 JPEG 시퀀스로 저장(`logs/events/`). `fusion/alert_policy.py`: 위험 '상태'를 경보 '이벤트'로 변환(엣지 트리거+히스테리시스). `vision/auto_exposure.py`: 자동 노출 제어 법칙(순수 로직 — 아래 §9). `scripts/`: RKNN 모델 변환/벤치마크·로그 수집/분석·센서 실측 유틸.
* 입력(비전, 센서)과 출력(오디오)은 반드시 추상화 계층(HAL) 인터페이스를 적용하여, 현재의 PC 모킹 클래스와 추후 Orange Pi 5 하드웨어 제어 클래스를 쉽게 교체할 수 있도록 구현.
* 상수 및 임계값은 매직 넘버 대신 `config.py`에 분리.
* 타입 힌트와 구글 스타일 Docstring 필수 작성. 예외 처리 철저.

## 3. Core Logic (Sensor Fusion)
* **High Risk:** 객체 인식됨 & ToF 거리 <= 100cm & Confidence >= MIN_CONFIDENCE. (즉각 경고 트리거)
* **Mid Risk:** 객체 인식됨 & ToF 거리 <= 150cm. (주의 경고 트리거)
* **`탐지 0개`와 `비전 신뢰 불가`는 다른 상태다.** 예전에는 `max_conf < MIN_CONFIDENCE` 하나로 뭉뚱그렸는데, 디텍터가 이미 conf로 필터링하므로 그 식은 `len(detections)==0`과 동치였다. 그 결과 빈 장면에서도 객체 확인 게이트가 우회되어 ToF 숫자만으로 경보가 나갔다 (2026-07-29 야외 `tof_only_ratio` 96.2%). `FusionEngine.evaluate()`는 세 상태를 분리한다:
  * **`vision_blind=True`** → 거리만으로 MID/HIGH (ToF 단독 안전망 유지). `tof_only_mode=True`.
  * **유효 탐지 있음** → 기존 융합 경로.
  * **비전 정상 + 탐지 0개** → **HIGH(100cm)만.** MID는 억제하고 `mid_suppressed`로 센다. 카메라가 멀쩡히 보는데 못 찾았으면 지면·담벼락일 가능성이 높다 (실측: MID 밴드를 분당 10.1회 왕복). 근접은 나뭇가지·간판 등 COCO 미포함 장애물의 안전망이라 남긴다.
* **`vision_blind`는 밝기만이 아니다 (`main._update_luma_blind` + 합성).** 프레임 밝기 밴드 이탈(암흑·화이트아웃)에 더해 **FPS fallback과 비전 stall도 반드시 포함**해야 한다. FPS fallback은 `last_detections = []`를 강제 주입하므로, 이를 "비전 정상 + 탐지 0개"로 넘기면 **비전이 죽은 바로 그 순간 MID 안전망이 꺼진다.** 회귀 테스트: `tests/test_fps_fallback.py::test_fallback_must_be_reported_as_vision_blind`.
  * 밝기 판정은 히스테리시스 + 연속 프레임 디바운스를 건다. AE 수렴 과도기(1~2초)에 모드가 깜빡이면 MID 경보가 되살아난다.
* **FPS Fallback은 `main._should_fps_fallback()`이 판정한다.** 저전력(4 FPS)·발열 스로틀링(5 FPS)·**TTS 발화 중 페이싱(8 FPS)** 은 모두 `FPS_FALLBACK_THRESHOLD`(8) 이하라, 제외하지 않으면 **의도적으로 FPS를 낮추는 순간 비전이 꺼져 모드가 스스로를 무력화한다** (2026-07-28 Pi 실측: 저전력 진입 0.7초 뒤 ToF 단독 전환). Fallback은 카메라 멈춤·과부하 같은 예기치 못한 붕괴만 잡는다. 저전력 구간의 진짜 멈춤은 비전 워커 Watchdog(`_check_vision_stall`)이 담당한다.
  * ⚠️ **TTS 제외가 특히 중요하다.** 발화는 경보를 말하는 순간, 즉 장애물이 잡힌 바로 그때 일어난다. 빠뜨리면 **경보마다 비전이 실명 처리되어 ToF 단독 모드로 넘어간다.** `TTS_ACTIVE_VISION_FPS`는 저전력·발열과 달리 임계값보다 낮은 게 아니라 **정확히 같아서**(둘 다 8) EMA 실측이 경계를 오르내렸고, 그래서 발견이 늦었다 (2026-08-05 실측: 발화 0.85~1.1초 뒤 진입이 매번 재현, 분당 9회 진입/해제). 회귀 테스트: `tests/test_fps_fallback.py::test_tts_active_does_not_trigger_fallback`.
  * 판정에는 `_update_luma_blind`와 같은 두 겹을 건다 — 히스테리시스(`FPS_FALLBACK_RECOVERY`)와 연속 프레임 디바운스(`FPS_FALLBACK_DEBOUNCE_FRAMES`). **해제선을 높게 잡으면 안 된다.** 이 기기의 비전 FPS는 평상시 8 근처라(2026-08-04 야외 fallback 34.5%) 해제선이 10 이상이면 한 번 걸린 fallback이 영영 안 풀려 진동보다 나쁜 상태가 된다.
* ToF 센서 값은 노이즈 제거를 위해 이동 평균 필터(window=3) 적용. **단 신규 물리 샘플일 때만 전진시킨다** — 메인 루프는 15Hz인데 ToF 실측은 ~4.8Hz(`TOF_INTER_MEASUREMENT_MS=210`)라, 게이트가 없으면 같은 값이 3번씩 버퍼에 들어가 평활 효과가 0이 되고 OoR 소프트 리셋도 물리 측정 1회 만에 트리거된다. 게이트는 **값 비교가 아니라 `BaseToFHAL.sample_seq` 기반**이다 (같은 거리가 연속 측정되는 것은 정상이므로 값으로는 중복을 판별할 수 없다). `evaluate(distance_is_new=...)`의 기본값 `True`는 "호출 1회 = 샘플 1개"인 테스트 관점이다.
* **VL53L1X는 범위를 벗어나도 0을 반환하지 않는다 — 그럴듯한 숫자를 뱉는다.** 이게 이 프로젝트에서 가장 오래 속았던 함정이다. 표적이 확실히 없는 방향(야외 5m 트인 공간)에서 **508샘플 전부가 무효 판정인데 거리는 34~321cm(SHORT 중앙값 120.8cm)로 정상처럼 나왔다** (2026-08-08 실측). 그 대역이 하필 경보 임계값(HIGH 100 / MID 150cm) 한복판이라 빈 공간에서 경보가 계속 나갔다. **거리 크기로는 절대 못 거른다.**
  * ⚠️ **허수값의 대역은 고정이 아니다 — 대역 기반 필터를 만들 수 없다.** 2026-08-08 야외에선 중앙값 120.8cm, 2026-08-09 실내에선 **30.8cm**로 나왔다. 후자는 HIGH(100cm)보다 더 안쪽이라 "100~124cm를 의심한다" 같은 규칙으로는 아예 안 잡힌다. 두 번의 실측이 같은 결론을 가리킨다 — **`RangeStatus`가 유일한 판별 수단이다.**
  * 구분 수단은 `RangeStatus` 하나뿐이다. pimoroni 래퍼가 `getDistance()`만 노출하며 버리지만, `.so`에는 ST 원본 API가 있고 래퍼 자신이 `self._dev`를 원본 API에 직접 넘긴다(`VL53L1X.py:223`) — 그 경로로 `VL53L1_GetRangingMeasurementData`를 ctypes 호출해 읽는다 (`_read_status`). 판별력은 **표적 없음 0/508 · 0/127, 실내 벽 41/41 · 41/41** (4회 실측, 신호 강도도 0.00~0.44 vs 4.97~5.87 MCPS로 10배 이상 갈린다).
  * ⚠️ **거리도 구조체 값을 써야 한다.** `get_distance()`와 구조체 읽기는 서로 다른 측정을 볼 수 있어서(값이 튀는 구간에서 최대 91% 불일치), 섞으면 한 측정의 status가 다른 측정의 거리를 판정한다.
  * ⚠️ **구조체 오프셋 검증을 "값이 다르면 실패"로 하면 안 된다.** 불일치 건수가 거리 표준편차와 비례한다(실내 σ0.1cm→0건 / 야외 σ24cm→7% / σ93cm→91%). 오프셋 오류라면 σ와 무관하게 깨진다 — 판정은 3σ 기준으로 한다(`scripts/tof_status_probe.py`). 이걸 오판해 멀쩡한 야외 데이터를 버릴 뻔했다.
  * status 조회 실패 시 예외를 올리지 않고 게이트만 끈다 (ROI와 같은 원칙).
  * **상한 게이트도 필요하다.** 센서 상태가 꼬이면 `getDistance()`가 65535mm를 뱉는데, 그대로 흘리면 6553.5cm가 되어 OoR 집계(400cm)를 빠져나간다 — **고장이 로그상 "그냥 멀리 있음"으로 보인다.**
* **I2C 통신 실패는 "측정 결과"로 위장한다 — ToF 데이터를 읽기 전에 통신부터 검증한다.** 위 함정과 같은 구조가 한 계층 아래에서 반복된다. 파이썬은 ctypes 콜백 안에서 난 예외를 삼키므로(`Exception ignored on calling ctypes callback function`), 배선이 끊겨도 C 라이브러리는 **0으로 채워진 버퍼를 정상 응답으로 처리**하고 `VL53L1_GetRangingMeasurementData`는 성공(0)을 반환한다. **반환값으로도 거리값으로도 감지되지 않는다.** 2026-08-09에 I2C 실패 227건 상태의 리포트(유효 0/66, 중앙값 0.0cm, 0.02 MCPS)를 "지면이 안 보인다"로 읽어 STEP 1을 잘못 닫을 뻔했다 — 커넥터 재체결 20분 뒤 같은 센서가 41/41 유효였다.
  * 감지 경로는 **`sys.unraisablehook` 하나뿐이다** (`scripts/tof_status_probe.py`의 `_record_unraisable`). 진단 스크립트는 실패 건수를 세어 **0건이 아니면 판단 자체를 막는다.**
  * `i2cdetect`에서 0x29가 보이는 것은 통신 정상의 근거가 안 된다 (주소 응답만 되고 데이터 전송이 깨진 상태가 실재한다).
  * **재발 조건은 "물리적으로 건드린 직후"다** — 8/7 손으로 들고 측정, 8/8 손으로 들고 이동, 8/9 각도 변경, **8/9 야외 보행(진동)**. 센서 고장이 아니라 간헐 접촉 불량이다. **각도·거치를 바꾼 뒤에는 반드시 I2C 실패 0건을 확인하고 본 측정에 들어간다.**
  * ⚠️ **`VL53L1XHAL.start()`는 재초기화용이기도 하므로 반드시 `_stop_polling()`을 먼저 부른다.** 안 그러면 옛 폴링 스레드가 살아 있는 채 `self._tof`가 아직 `open()` 전인 객체로 교체되고, 그 스레드가 `_dev`(=`None`)를 원본 API에 넘겨 **NULL 역참조로 SEGV**가 난다. **ctypes는 `argtypes`가 `c_void_p`면 `None`을 NULL로 조용히 통과시키므로 `try/except`로 절대 못 잡고 프로세스가 통째로 죽는다.** 2026-08-09 야외 보행에서 I2C 접촉 불량 → 재초기화 → SEGV로 서비스가 **19회 재시작**했다 (사용자에게는 "야외에서 전원이 껐다 켜졌다"로 보였다 — 실제로는 보드 부팅 1회, 프로세스만 크래시 루프). 폴링 스레드가 제한 시간(`TOF_POLL_JOIN_TIMEOUT_SEC`) 안에 안 멈추면 `self._tof`를 건드리지 않고 예외를 올린다 — 재초기화가 실패해도 ToF만 죽고 비전은 계속 돈다. 회귀 테스트: `tests/test_sensor.py::TestVL53L1XReinitSegfaultGuard`.
  * **"전원이 나갔다"와 "프로세스가 죽었다"를 먼저 가른다.** `last -x reboot`으로 실제 부팅 횟수를 세고, `journalctl | grep 'Main process exited'`로 종료 코드를 본다. `code=killed, status=11/SEGV`면 전원·발열이 아니라 네이티브 크래시다. 세션마다 CSV가 새로 생기므로(§4) **`logs/*.csv` 개수와 0바이트 여부가 재시작 횟수의 가장 빠른 지표**다.
  * ⚠️ **진짜 전원 급단은 "기록이 있다"가 아니라 "기록이 통째로 없다"로 나타난다.** Pi는 `orangepi-ramlog`로 저널을 RAM에 두고 종료 시 디스크로 내리므로, 전원이 예고 없이 끊기면 **그 부팅 구간의 저널과 `wtmp`(=`last`) 항목이 함께 유실된다.** 2026-08-09 21:38 보행이 그랬다 — CSV 10.5분치가 정상값에서 뚝 끊겼는데 `journalctl --list-boots`에도 `last -x reboot`에도 그 부팅이 없었다. **`Main process exited`를 못 찾은 것이 "SEGV가 아니다"인 동시에 "전원 사고다"의 증거**다 (같은 날 13:02의 SEGV 19회는 저널에 그대로 남았다). 사용자 눈에는 둘 다 "전원이 껐다 켜졌다"로 똑같이 보이므로 **`--list-boots`부터 본다.**
* **ToF는 정면(수평~살짝 위)을 향한다. 아래로 기울이지 않는다** (2026-08-09 결정, `docs/2.1_ROADMAP.md` §0-0). 1점 27° 원뿔이라 지면으로 기울이면 그 하나가 지면에 묶여 **정면의 사람·기둥·차를 못 잰다.** 지면+정면 동시 측정은 VL53L5CX(8×8존)가 있어야 성립하는데 이번 사이클에선 안 산다.
  * 부수 효과: 수평 0°면 원뿔 하단이 지면에 닿는 지점이 `h/tan(13.5°) = 128/0.240 = 533cm`로 MEDIUM 상한(약 300cm) 밖이라 **지면 오염이 기하학적으로 불가능하다.** 그러면서 2m 지점에서 80~176cm(가슴~머리)를 커버한다.
* **~~ToF 시야(ROI)로 지면 반사를 배제한다~~ — 이 계획의 근거는 2026-08-08에 무효화됐다** (`TOF_ROI_*`, `VL53L1XHAL._apply_roi`). 근거였던 "2026-08-04 야외 로그에서 유효 측정의 92%가 100~124cm에 몰렸다 → 지면 반사"에서, **그 값들이 지면이 아니라 위의 허수값이었다.** 기하학적으로도 안 맞았다 — 가슴 높이 128cm면 지면까지 직선 거리가 최소 128cm라 100~124cm는 나올 수 없다. **잘라낼 지면 반사가 존재하지 않으므로 ROI로 얻을 것이 없고, 반대쪽을 자르면 머리 높이 장애물을 잃는 위험만 남는다.** `TOF_ROI_ENABLED`는 False로 둔다.
  * ⚠️ (훗날 다른 목적으로 ROI가 필요해질 때를 위해) **격자 Y축이 장면의 위/아래 중 어디에 대응하는지는 코드로 알 수 없다** (카메라부터가 상하 반전 장착이라 `CSI_ROTATE_180=True`). `scripts/tof_roi_probe.py`로 실측한 뒤에만 켠다.
  * ⚠️ `start()`의 aarch64 ctypes 패치 블록에 **`setUserRoi.argtypes`가 반드시 있어야 한다.** `initialise.restype = c_void_p`라 핸들이 Python int인데, `argtypes` 없이 넘기면 32비트 C int로 잘려 segfault 난다. 그 블록이 존재하는 이유가 정확히 이 버그다.
  * ROI 적용 실패는 예외를 올리지 않고 경고만 남긴 뒤 전체 FoV로 계속한다 — 시야가 넓은 것보다 센서가 아예 없는 쪽이 훨씬 나쁘다.
  * ⚠️ 센서 데이터 만료로 안전 거리를 주입할 때는 `distance_is_new=True`로 넘겨야 한다. 게이트에 걸리면 만료 이전 거리가 필터에 남아 경보가 계속 나간다.
* **경보 발화는 `fusion/alert_policy.py`가 게이트한다.** 위험 판정(`RiskLevel`)은 "거리 <= 임계값"인 **상태**라 그대로 흘리면 매 사이클 경보가 나간다 (2026-07-28 야외 실측: HIGH 상태 55.8%, 비프 분당 181회). `AlertPolicy`는 위험 수준이 **올라가는 순간**에만 통과시키고, 지속 중에는 `ALERT_REMINDER_SEC` 간격 리마인더만 허용한다. 해제는 `임계값 + ALERT_HYSTERESIS_CM`을 넘어야 이뤄져 임계값 근처 진동을 흡수한다.
  * 음소거 해제(`_toggle_mute`)와 ToF 센서 재초기화(`_on_sensor_reinit`) 시 반드시 `AlertPolicy.reset()`을 호출한다. 래치가 남으면 실제 위험을 놓친다.
  * 배터리 등 **시스템 경고는 정책을 우회**한다 (장애물 경보가 아니므로).
  * OoR(`TOF_OUT_OF_RANGE_CM`)은 히스테리시스와 무관하게 래치를 해제한다 — 측정 상한이 해제선보다 짧은 레인징 모드에서 영원히 안 풀리는 것을 막는 백스톱.

## 4. Development & Testing
* 비전 AI 환경: PC는 Linux x86_64(CPU)로 개발 — GPU 가속 불필요. 실제 추론은 Orange Pi 5 NPU(RKNN)에서 수행되므로 PC에서는 정확도/로직 검증 목적으로만 YOLO를 CPU로 돌린다.
* 테스트: `pytest` 프레임워크 사용. (핵심 케이스: 거리 임계값 경계 조건, Fallback 전환 로직, 모킹 객체를 활용한 오탐지 테스트).
* 로깅: 로컬 CSV 파일에 1초 1회 기록 (timestamp, cpu_temp, fps, tof_distance_cm, alert_triggered, latency_ms, tts_spoken, occlusion_alerts, alerts_emitted, tof_raw_cm, tof_only_ratio, frame_luma, no_detect_ratio, mid_suppressed, exposure, gain).
  * **CSV는 세션(프로세스 실행)마다 새 파일**(`logs/raseyes_log_<타임스탬프>.csv`)에 쓴다. 단일 경로에 `mode="w"`로 쓰던 방식은 재시작마다 직전 세션을 덮어써 2026-07-28 야외 테스트 로그를 통째로 날렸다. Pi에 RTC 배터리가 없어 부팅 시 시계가 되감기므로, 파일명이 충돌하면 일련번호를 붙여 **절대 덮어쓰지 않는다**.
  * 뒤 6개 컬럼은 진단용이다. `alerts_emitted`(실제 발화 횟수)만 보면 ToF가 통째로 OoR이 되어 조용해진 경우를 개선으로 오판하므로, `tof_raw_cm`(OoR 비율 산출)과 `tof_only_ratio`(비전 실명 정도)를 함께 본다. `analyze_logs.py`가 OoR 70% 초과 시 "센서 실명 의심"을 표시한다.
  * `frame_luma`(노출 건강도 — 화이트아웃/암흑 비율 산출), `no_detect_ratio`(탐지 밀도 — `tof_only_ratio`가 실명 전용이 되며 빠진 정보), `mid_suppressed`(5-2가 억제한 MID 횟수)는 노출 제어 도입 이후 컬럼이다. **`mid_suppressed`는 억제로 놓친 장애물을 사후 검증할 유일한 수단**이다 (클립은 HIGH에서만 찍힌다).
  * `exposure`/`gain`은 2026-08-04 이후 컬럼이다. **노출이 상한에 붙어 있던 비율이 모션블러 진단의 핵심 지표**이고(`analyze_logs.py`가 30% 초과 시 경고), 게인 상한 비율은 노출을 낮춘 대가로 늘어난 노이즈를 본다. 둘을 함께 봐야 `CSI_AE_EXPOSURE_MAX`를 어느 방향으로 옮길지 정할 수 있다 — 게인까지 동시에 상한이면 광량 자체가 부족한 것이라 상한을 낮춰도 밝기만 잃는다.
  * ⚠️ **`analyze_logs.py`에서 새 컬럼을 결측 시 0으로 집계하면 안 된다.** 예전 아카이브가 "전부 암흑"으로 오독되어 날짜 간 비교가 통째로 망가진다. 기존 `if r.get(key)` 필터 패턴을 그대로 쓰고, 없으면 "미측정"으로 표시한다.
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
| `python3 scripts/tof_status_probe.py --mode medium --seconds 30` | ToF RangeStatus 진단 (**Pi에서 실행, 서비스 정지 필요**). 거리값이 실제 측정인지 "표적 없음" 허수값인지 판정 — 센서 이상·오경보 조사의 1차 도구. **리포트에서 `[I2C 통신] 실패 N건`을 제일 먼저 볼 것** — 0건이 아니면 나머지 숫자는 측정이 아니다 |
| `python3 scripts/tof_roi_probe.py` | ToF 시야(ROI) 방향 실측 (**Pi에서 실행, 서비스 정지 + 착용 각도 필요**). ⚠ ROI 자체는 근거가 무효화되어 보류 상태다(§3 참고) |
| `python3 scripts/wb_probe.py --frames 30` | 카메라 채널 균형 진단 (**Pi에서 실행, 서비스 정지 필요**). AWB 도입 판단은 2026-08-04 수집 클립 분석으로 이미 끝났다(§10) — AWB 게인이 의심스러울 때 센서 원본 채널비를 직접 재는 용도로 남긴다 |
| `python scripts/export_rknn.py` | YOLOv8n → RKNN INT8 변환 (**PC x86에서 실행**, `rknn-toolkit2` 필요). 결과물을 Pi로 scp |
| `python scripts/bench_rknn.py` | RKNN 추론 속도 실측 (**Pi에서 실행**). KPI(평균 <60ms) 달성 여부 판정 |
| `python scripts/test_device.py` | Orange Pi 5 HAL 단품 테스트 (**Pi에서 실행**). 카메라·ToF·오디오 순차 검증, 전체 PASS 시 exit(0) |
| `sudo python scripts/pwm_fan_control.py` | 액티브 쿨러 PWM 온도 기반 제어 (**Pi에서 실행, main.py와 별도 프로세스**) |

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
* **TTS 스택:** PiperTts → EspeakTts → MockTts 우선순위. 모델 설치: `bash scripts/download_piper_model.sh`.
  * ⚠️ **Piper 모델이 영어(`models/tts/en_US-lessac-medium.onnx`)인 것은 의도적이다.** 한국어 `ko_KR-kss-medium`의 합성 음질이 어색해 영어 모델을 선택했다. 한국어 프로젝트에 영어 TTS라 버그로 오인하기 쉬우므로(2026-08-06에 두 번 지적됨) **되돌리지 않는다.** 설정 위치는 `config.TTS_PIPER_MODEL_PATH`.
* **고정 문구 사전 렌더링 캐시:** `audio/prerendered_tts.py` + `scripts/prerender_tts_cache.py`. 부팅 직후처럼 고정 경고 문구가 몰리는 상황에서 매번 Piper 신경망 추론을 돌리면 연산·전류 스파이크가 생기므로, 빌드 타임에 WAV로 미리 렌더링해두고 런타임엔 로드만 한다. 문구 목록(`config.TTS_PRERENDERED_PHRASES`)이나 모델이 바뀌면 재실행 필요.
* **믹서 음소거 해제(`JackAudioHAL._unmute_hw`)는 `Headphone`/`Speaker`를 켠다** (`audio.jack_hal.MIXER_OUTPUT_SWITCHES`). `hp switch`/`spk switch`는 **DAPM 위젯**이라 재생 스트림이 활성인 동안만 on을 유지하고, 유휴 상태에서는 커널이 즉시 off로 되돌린다 (2026-08-06 Pi 실측: `amixer sset ... on` 직후 `sget`이 `[off]`).
  * 그래서 그 둘로 검증하면 **매 기동마다 "믹서 음소거 해제 검증 최종 실패" 에러를 남기면서, 정작 소리를 막고 있는 `Headphone`이 off인 것은 놓친다** — 실제로 부팅 안내가 통째로 무음이었는데 서비스는 정상으로 보였다. 로그의 `부팅 오디오 큐 재생`은 재생 시도만 뜻하므로 무음 진단에 쓸 수 없다.
  * 무음이 의심되면 `ssh raseyes "amixer -c 2 sget Headphone"`으로 확인한다. ES8388은 **card 2**다 (card 0은 DisplayPort, 1은 HDMI — `amixer -c 0`은 엉뚱한 카드다).
  * 회귀 테스트: `tests/test_audio.py::TestJackAudioUnmute`.
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

## 9. Camera Auto Exposure (AE)
* **OV13855 드라이버에는 자동 노출·AWB가 없다** (2026-07-29 Pi 실측). `/dev/video11`은 제어 가능한 컨트롤이 0개고, 센서 subdev(`/dev/v4l-subdev2`)에는 `exposure`(4~3210)·`analogue_gain`(128~1984)·`vertical_blanking` 수동 컨트롤만 있다. `auto_exposure`/`white_balance_auto`/`red_balance`/`brightness`는 **존재하지 않으며**, rkaiq 3A 데몬도 미설치에 OV13855용 IQ 파일도 없다(`ov13850`은 이름만 비슷한 다른 센서). 그래서 AE를 직접 구현한다.
* **제어 법칙은 `vision/auto_exposure.py`에 순수 로직으로 분리한다.** I/O·시계 호출 없이 `update(now, frame)`만 받으므로 하드웨어 없이 PC에서 수렴·헌팅을 검증할 수 있다. v4l2 쓰기는 `CSICameraHAL._set_exposure_gain()`이 담당한다.
* **평균 휘도만 보면 부분 화이트아웃에 속는다.** 절반이 날아가도 평균은 목표치 근처에 남고, 평균 자체가 255에서 포화해 과노출 배율을 과소평가한다. 클리핑 픽셀 비율(`CSI_AE_CLIP_LIMIT`)을 함께 보고, 초과 시 평균과 무관하게 고정 배율(`CSI_AE_CLIP_STEP`)로 기하급수 감광한다.
* **데드밴드 안에서는 v4l2 호출이 발생하지 않는다.** subprocess 비용은 Pi 실측 중앙값 4.5ms(프레임 예산 66ms의 7%)라 수렴 후에도 매번 부르면 낭비다. 정상 상태 비용은 측광(~0.3ms)뿐.
* **적용 직후 `CSI_AE_SETTLE_SEC` 동안은 측광을 건너뛴다.** 센서에 노출을 써도 2~3프레임 뒤에 반영되므로, 그 사이 같은 방향으로 또 스텝을 밟으면 오버슛 → 헌팅이 된다.
* **노출을 상한까지 먼저 쓰고 모자랄 때만 게인을 올린다** (게인이 노이즈원). `CSI_AE_EXPOSURE_MAX`가 모션블러 상한 노브다 — 낮추면 어두운 곳을 게인(노이즈)으로 채운다. 보행 웨어러블이라 블러가 불리하므로 야외 실측 후 튜닝한다.
* **`AutoExposure.settled`는 "값이 안 바뀌었다"가 아니라 "AE가 급하지 않다"이다** (`CSI_AE_URGENT_LUMA_ERROR` 기준). 데드밴드로 정의하면 조도가 늘 변하는 야외에서 AE가 상시 조정 중이라 저전력 모드가 영영 걸리지 않아 전력이 회귀한다. 레일(하드웨어 상·하한)에 걸린 경우도 True다 — 밤처럼 영원히 수렴 못 하는 상황에서 FPS를 붙들면 배터리만 소모한다.
* **AE 미수렴 중에는 저전력 모드 진입을 미룬다** (`main.py`의 진입 조건에 `self._vision.ae_settled` AND). 저전력 4 FPS에서는 프레임이 250ms 간격으로만 들어와 수렴이 배로 느려지는데, 그 구간이 정확히 그늘→직사광 전환처럼 AE가 가장 급한 순간이다.
* **E2E 레이턴시는 조도나 I2C가 아니라 FPS를 따라간다 — 저전력 4 FPS 구간이 레이턴시를 3배로 만든다** (2026-08-09 두 세션 실측: `fps<8` 259~372ms vs `fps≥8` 111~127ms). 프레임이 250ms 간격으로만 들어오니 구조적 비용이다.
  * ⚠️ **원인을 I2C 예외로 오진하기 쉽다.** 8/9 낮 세션은 I2C 실패가 초당 수십 건 쏟아진 상태라 레이턴시 2배를 그 비용으로 읽었는데, **I2C가 멀쩡했던 밤 세션에서 같은 분리가 나와** 뒤집혔다. 조도도 아니다 — 낮 세션은 **밝을 때**(AE 수렴) 밤 세션은 **어두울 때**(AE 레일) 저전력에 들어갔는데 양쪽 다 FPS를 따라갔다.
  * 진입 시점이 `vision_blind`인 순간과 겹치면 **비전이 죽은 채로 ToF 반응까지 느려진다** (2026-08-09 야간 보행 p95 496ms — KPI 500ms 턱걸이). 야간 지원을 하기로 하면 `vision_blind` 중 진입 차단을 함께 검토한다.
* **AE 경로의 subprocess 타임아웃은 짧게 잡는다**(`CSI_AE_CTRL_TIMEOUT_SEC=0.5`). `_setup_isp_pipeline()`의 5초를 그대로 쓰면 v4l2-ctl이 멈췄을 때 비전 워커가 5초 블로킹되어 `VISION_STALL_THRESHOLD_SEC`(2.0)를 넘긴다.
* `CSI_SENSOR_EXPOSURE`/`CSI_SENSOR_GAIN`은 **AE의 시드값**이다. 예전처럼 실내 최적값(3000/게인 최대치)을 박아두면 야외에서 부팅할 때 첫 수 초가 완전 화이트아웃이다 — 한쪽 극단이 아니라 중간에서 출발한다.
* **노출 상한(`CSI_AE_EXPOSURE_MAX`)은 모션블러 노브다 — 하드웨어 상한을 그대로 쓰면 안 된다.** 2026-08-04 야외 실측에서 3210(하드웨어 최대)까지 열어둔 결과, 프레임 휘도는 정상(luma 104~127)인데 선명도 하위 프레임은 화이트밸런스를 보정해도 탐지가 0건이었다 (선명도 상위 3장 4건 vs 하위 3장 0건). 1600으로 낮췄다. 다만 **블러의 원인이 노출 시간인지 보행 중 움직임인지는 아직 갈리지 않았다** — 그래서 `exposure`/`gain`을 CSV에 남긴다.

## 10. Camera Auto White Balance (AWB)
* **녹색 캐스팅의 원인은 채널 게인 미보정으로 확정됐다** (2026-08-04). 판별 근거는 두 가지다: ① 회색이어야 할 **보도블록 패치**만 잘라 재도 R/G=0.56~0.65, B/G=0.65~0.84로 (장면의 자연 초록이 아니다), ② 이미지 구조(건물·차량 윤곽)가 멀쩡하다 (디모자이크/픽셀포맷 불일치가 아니다). 베이어 센서의 G 픽셀이 R/B의 2배인데 게인 보정이 없어 생기는 전형적인 미보정 raw다.
* 드라이버에 `red_balance`/`blue_balance`가 없어(§9) 센서에 되먹일 수 없다. **소프트웨어 그레이월드**를 `vision/auto_white_balance.py`에 AE와 같은 순수 로직으로 두고, 캡처된 프레임에 채널 게인을 곱한다.
* **AWB는 AE *다음에* 적용한다.** AE는 센서 노출을 되먹이는 폐루프라 센서가 실제로 낸 값(보정 전)을 측광해야 하고, 추론·클립은 색이 교정된 프레임을 봐야 한다.
* **게인은 특정 채널을 1.0으로 고정하지 말고 채널 평균을 전체 평균에 맞춘다.** G를 1.0에 묶고 R/B를 올리면 전체가 밝아져 AE 루프와 간섭한다.
* **측광에서 암부·클리핑 픽셀을 제외한다.** 클리핑 픽셀은 세 채널이 모두 255라 게인 추정을 무채색 쪽으로 망가뜨리고, 암부는 노이즈가 색을 지배한다.
* **그레이월드는 한 색이 지배하는 장면(잔디밭 등)에서 과보정한다.** `CSI_AWB_GAIN_MIN/MAX` 클램프가 그 상한선이고, EMA(`CSI_AWB_SMOOTHING`)가 장면 전환 시 색 출렁임을 막는다. 클램프가 계속 걸리면 경고가 뜨므로 가정이 깨진 환경임을 알 수 있다.
* 비용은 정상 상태 LUT 적용뿐이다 (PC 실측 중앙값 0.28ms — 프레임 예산 66ms의 0.43%). 게인 재계산은 `CSI_AWB_UPDATE_INTERVAL_SEC` 주기에만 1.0ms.
* 수집된 클립에 이 클래스를 그대로 적용해 PC에서 YOLO를 돌리면 탐지가 4건 → 6건으로 늘고, 오분류도 정정된다(`bench:0.43` → `car:0.45`). **다만 선명도가 낮은 프레임은 보정해도 0건이다** — AWB는 블러를 고치지 못한다.
