# 2026-07-07 작업 내용

## 1. 보조배터리 순간 전류 스파이크 완화 — 컴포넌트 차등 기동

### 배경
Orange Pi 5는 보조배터리 단독 구동을 목표로 하는데, 부팅 시 NPU/카메라/센서/오디오가 동시에 초기화되면 순간 전류 스파이크가 겹쳐 보조배터리 과전류 보호(OCP)가 트립될 위험이 있음. 이를 완화하기 위해 컴포넌트를 동시에 켜지 않고 순차(차등)적으로 기동하도록 구현.

### 구현
- `config.py`: `STARTUP_STAGGER_SEC = 1.5`(초) 상수 추가 — 컴포넌트 순차 기동 간 지연시간.
- `main.py`의 `RasEyesApp.start()`: `use_hw=True`일 때만 stagger 적용 (Mock 모드는 지연 없이 즉시 기동).
  1. `vision.start()` → 1.5초 대기
  2. `sensor.start()` → 1.5초 대기
  3. `audio.start()` (즉시, CSV 로거 오픈 포함)
  4. 버튼 핸들러 초기화
  5. → 1.5초 대기 → `BootSequence().play()`(부팅 안내음/TTS)
  6. `use_hw`일 때는 TTS 발화가 끝날 때까지 대기(`STARTUP_TTS_WAIT_TIMEOUT_SEC`) — TTS 합성 CPU 부하가 이후 NPU 연속 추론 부하와 겹치지 않도록 함
  7. → 1.5초 대기 → vision/sensor 워커 스레드 시작
- 상시적인 전류 스파이크 저감 조치도 함께 적용:
  - `audio/piper_tts.py`에서 `onnxruntime.SessionOptions`를 몽키패치해 `TTS_ONNX_INTRA_OP_THREADS=1`, `TTS_ONNX_INTER_OP_THREADS=1`로 제한 (기본값은 모든 CPU 코어 사용)
  - `JackAudioHAL`을 `sounddevice` 대신 `aplay` subprocess 방식으로 전환해 TTS와 동일한 ALSA dmix 경로 사용 (PortAudio 충돌 방지)
  - (이후 커밋 912de18에서) 상주 `ResidentAudioStream` 도입 — 재생마다 ALSA 디바이스를 열고 닫지 않고 상주 스트림을 유지해 코덱/앰프 반복 온오프로 인한 전류 스파이크 방지 (`CLAUDE.md` §7에 규칙화)

### 검증
- 보조배터리 단독 구동 테스트 통과 (2026-07-09 Orange Pi 5 배포 후 재확인, 본 문서 하단 항목 참고)

## 2. 한국어 TTS → 영어 TTS 전환

### 배경
Piper TTS로 한국어 음성 출력을 우선 구현. 다음 이슈들을 해결하며 한국어 파이프라인을 완성했었음:
- `rhasspy/piper-voices`에는 공식 한국어 모델이 없어, KSS 한국어 데이터셋으로 학습된 커뮤니티 유지보수 모델(`neurlang/piper-onnx-kss-korean`)로 `scripts/download_piper_model.sh`를 수정해 다운로드
- 해당 모델의 `phoneme_type`이 `pygoruut`로 정의되어 있어 `piper-tts` 로딩 시 `'pygoruut' is not a valid PhonemeType` 오류로 EspeakTts로 fallback되는 현상 발견 → `audio/piper_tts.py` 로딩 시점에 `piper.config.PhonemeType`/`piper.voice.PhonemeType` Enum을 동적으로 확장하고 `pygoruut` 패키지를 lazy import하는 몽키패치를 적용해 해결
- Orange Pi 5에 배포해 `PiperTts 초기화 완료`, `부팅 오디오 큐 재생` 로그까지 에러 없이 정상 구동하는 것까지 확인

### 전환 이유
실제 기기에서 들어본 한국어 합성 음성의 발음/억양이 너무 어색해 실사용에 부적합하다고 판단, 최종적으로 영어 TTS로 전환 결정.

### 변경 내용 (커밋 f761cfb)
- `config.py`: `TTS_PIPER_MODEL_PATH`를 `models/tts/en_US-lessac-medium.onnx`로 변경
- `scripts/download_piper_model.sh`: 다운로드 대상을 한국어 커뮤니티 모델에서 `rhasspy/piper-voices`의 영어 공식 모델 `en_US-lessac-medium`으로 변경
- 안내 문구(부팅 시퀀스, 위험 경고 TTS 문구 등)를 영어 문구로 교체
- 한국어 전용으로 추가했던 `pygoruut` 몽키패치 코드는 영어 모델(`phoneme_type=espeak`)에서는 해당 분기를 타지 않아 동작에 영향이 없으므로 삭제하지 않고 코드에 그대로 남겨둠
