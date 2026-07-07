# 2026-07-07 작업 내용

## TTS 품질 개선 — espeak-ng → Piper TTS 전환

### 배경
espeak-ng 특유의 로봇 음성 품질 문제와 메시지가 정돈되지 않은 느낌을 해결하기 위해
신경망 기반 Piper TTS로 전환.

### 신규 파일
| 파일 | 내용 |
|------|------|
| `audio/piper_tts.py` | PiperTts 구현체 (BaseTtsHAL 상속) |
| `scripts/download_piper_model.sh` | 모델 다운로드 스크립트 |

### 수정 파일
| 파일 | 내용 |
|------|------|
| `audio/__init__.py` | PiperTts re-export 추가 |
| `audio/tts.py` | non-blocking 재생 + finally + 레이스 컨디션 수정 |
| `config.py` | `TTS_PIPER_MODEL_PATH` 상수 추가 |
| `main.py` | `_build_tts()` 우선순위 변경, `_find_audio_device()` 분리 |
| `requirements.txt` | `piper-tts>=1.2.0` 추가 |
| `requirements-rpi.txt` | `piper-tts>=1.2.0` 추가 |
| `tests/test_tts.py` | TestPiperTts 9개 케이스 추가 |
| `CLAUDE.md` | Section 7 Audio Threading Rules 추가 |
| `ROADMAP.md` | 추가 구현 사항 섹션에 Piper TTS 전환 기록 |

### 코드 리뷰 피드백 반영 (3라운드)

**1차:**
- `sd.play(blocking=True)` → non-blocking + 50ms 폴링 루프로 교체
- 합성 루프 내 `_stop_flag` 조기 중단 적용
- TestPiperTts 단위 테스트 추가
- `requirements-rpi.txt` 누락 반영
- `os.path.exists()` 모델 파일 선행 검사 추가
- Google-style Docstring 보완

**2차:**
- `_kill_current` join timeout 1.0s → 0.1s (E2E latency 보호)
- `finally: sd.stop()` 으로 장치 해제 보장
- 빈 오디오 배열 (`len(audio) == 0`) 방어 코드 추가

**3차:**
- `_stop_flag.clear()` → `self._stop_flag = threading.Event()` 교체 (레이스 컨디션 근본 수정) — piper_tts.py, tts.py 모두 적용
- EspeakTts `_speak_worker`도 동일한 non-blocking + finally 패턴으로 통일

### 최종 테스트 결과
```
134 passed in 6.28s
```

### 설치 방법 (OPi5 포함)
```bash
pip install piper-tts
bash scripts/download_piper_model.sh
python main.py
```

---

## Orange Pi 5 배포 버그 수정 — 오디오 동시 재생 및 TTS 언어 변경

### 문제
1. **TTS 여러 목소리 동시 재생** — 장애물 감지 시 이전 발화가 끊기지 않고 겹쳐 들림
2. **비프음 + TTS 동시 재생** — 경보음과 음성이 동시에 출력되어 알아들을 수 없음
3. **한국어 TTS 품질 불량** — pygoruut 음소 변환 오류로 알아들을 수 없는 음성 출력

### 원인 분석

**버그 1 — stop_flag 교체 레이스 컨디션 (근본 원인)**
`_kill_current()`에서 `self._stop_flag = threading.Event()`로 교체 시,
이전 스레드가 새 Event를 참조 → `is_set()` == False → 종료 안 됨 → 두 aplay 동시 실행.

**버그 2 — PortAudio/ALSA 동시 접근 충돌**
`JackAudioHAL`이 `sounddevice.OutputStream`을 사용해 비프음을 재생하면서
PiperTts의 `aplay`와 동시에 ES8388 ALSA 장치에 접근 → 충돌 및 겹침.

**버그 3 — 한국어 모델 품질**
`ko_KR-kss-medium` 모델의 pygoruut 음소 변환 품질 문제.

### 수정 내용

#### `audio/piper_tts.py`
- `_start_thread()`: `stop_flag`를 생성 시점에 로컬 캡처하여 인자로 전달
  (`args=(text, stop_flag)`) → 스레드가 자신의 Event만 참조 (교체 영향 없음)
- `_kill_current()`: `self._current_proc`에 aplay proc 저장 → `proc.kill()` (SIGKILL)으로
  blocking `stdin.write()` 중인 스레드도 즉시 강제 종료. join timeout 0.5s로 증가
- `_speak_worker()`: `finally: self._current_proc = None` 제거 (새 스레드 proc를 덮어쓰는 버그)
- `is_speaking()` 메서드 추가
- `import numpy` 제거 (미사용)

#### `audio/jack_hal.py`
- `sounddevice.OutputStream` 완전 제거 → `aplay` subprocess로 전환
- float32 파형을 S16_LE int16으로 변환 후 aplay에 파이프
- sounddevice 의존성 제거, `start()`가 PortAudio 초기화 없이 동작

#### `audio/tts_hal.py`
- `is_speaking() -> bool` 기본 메서드 추가 (기본값 False)

#### `main.py`
- `_build_audio()`: `sounddevice` import 체크 → `aplay --version` 체크로 변경
- `_run_loop()`: TTS 발화 중에는 비프음 suppress
  (`not self._tts.is_speaking()` 조건 추가)
- `_build_tts_text()`: 영어 메시지로 전환
  - HIGH: `"Danger! {label}, {dist} centimeters, {direction}"`
  - MID: `"{label} {direction}"`
  - ToF only HIGH: `"Danger! Obstacle ahead"`
  - ToF only MID: `"Caution, obstacle"`

#### `config.py`
- `TTS_ESPEAK_VOICE`: `"ko"` → `"en"`
- `TTS_PIPER_MODEL_PATH`: `ko_KR-kss-medium.onnx` → `en_US-lessac-medium.onnx`

#### `scripts/download_piper_model.sh`
- 다운로드 대상: `en_US-lessac-medium` (rhasspy/piper-voices, HuggingFace)

#### `tests/test_tts.py`
- `threading` import 추가
- `test_speak_worker_*`: `stop_flag`를 명시적으로 생성해 인자로 전달
- `TestBuildTtsText`: 영어 출력 기준으로 전체 갱신

### 최종 테스트 결과
```
42 passed in 0.17s
```

### Orange Pi 5 배포 결과
- PiperTts 초기화: `en_US-lessac-medium.onnx` 로드 성공
- 오디오 출력: 비프음(aplay) + TTS(aplay) 모두 dmix 경유, 동시 재생 없음
- TTS 발화 예시: `"Danger! person, 80 centimeters, ahead"`
