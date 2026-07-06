# 2026-07-06 작업 내역

## 1. feedback.txt 반영 — BootSequence 누락 연동

**문제**: `audio/boot_sequence.py`는 구현되어 있었으나 `main.py`에서 호출하는 코드가 없어 부팅 멜로디·TTS가 실제로 실행되지 않음.

**수정**:
- `main.py` — `from audio.boot_sequence import BootSequence` import 추가
- `main.py:start()` 끝에 `BootSequence().play(self._audio, self._tts)` 호출 추가

---

## 2. 삐비빅 경보음 계속 울리는 버그 수정

**증상**: 서비스 실행 직후부터 5초마다 카메라 가림 경보음(삑삑삑)이 계속 울림.

**원인 분석**:
- 재부팅 후 MIPI ISP 파이프라인이 초기화되지 않아 `/dev/video11`이 검은 프레임 출력 (mean=1.8/255, frame delta=0.19)
- 프레임 변화량(0.19)이 임계값(3.0) 미달 → 15프레임 연속 → 가림 감지 false positive 발생
- 정상 화면의 정적 장면에서도 센서 노이즈로 인한 delta ≈ 1.89 → 임계값 3.0 미달로 false positive 지속

**수정**:
| 파일 | 변경 내용 |
|------|-----------|
| `vision/csi_camera_hal.py` | `_setup_isp_pipeline()` 추가 — `start()` 시 `media-ctl` + `v4l2-ctl`로 ISP 파이프라인 및 센서 노출 자동 설정 |
| `config.py` | `CAMERA_OCCLUSION_CHANGE_THRESH` 3.0 → 1.0 (정적 장면 노이즈 ≈ 1.89 > 1.0, 완전 차단 ≈ 0.19 < 1.0) |
| `config.py` | `CSI_SENSOR_SUBDEV`, `CSI_SENSOR_EXPOSURE`, `CSI_SENSOR_GAIN` 상수 추가 |

---

## 3. espeak-ng 설치 및 TTS 오디오 충돌 수정

### 3-1. espeak-ng 설치
- Orange Pi에 `espeak-ng` 미설치 상태 → MockTts fallback 되어 TTS 무음
- `sudo apt-get install -y espeak-ng` 설치 완료 (한국어 음성 `ko` 포함)

### 3-2. 부팅 멜로디 타이밍 버그
**원인**: 마지막 HIGH 비프(80ms, non-blocking) 재생 완료 전에 TTS subprocess가 ALSA 장치를 선점.

**수정**: `audio/boot_sequence.py` — 멜로디 루프 후 `time.sleep(AUDIO_BEEP_DURATION_MS/1000 + 0.05)` 추가.

### 3-3. TTS ALSA 장치 충돌 (핵심 버그)
**원인**:
- 기존 `EspeakTts`: `subprocess.Popen(["espeak-ng", ...])` → espeak-ng가 직접 ALSA `hw:2,0` 열기 시도
- JackAudioHAL이 sounddevice로 `hw:2,0`을 점유 중 → `Device or resource busy` 에러
- 이후 `EspeakTts`를 sounddevice 방식으로 교체했으나, espeak-ng가 22050Hz mono WAV 생성 / JackAudioHAL은 44100Hz stereo로 설정 → PortAudio가 스트림 재구성 실패 (`Error opening OutputStream`)

**수정**: `audio/tts.py` 전면 재작성
| 변경 전 | 변경 후 |
|---------|---------|
| `subprocess.Popen(["espeak-ng", text])` — espeak-ng가 ALSA 직접 접근 | `subprocess.run(["espeak-ng", "--stdout", text])` — WAV 데이터만 생성 |
| 프로세스 기반 비동기 | 스레드(`threading.Thread`) 기반 비동기 |
| ALSA 장치 충돌 | sounddevice로 단일 경로 재생 |
| 22050Hz mono로 재생 | 44100Hz stereo로 업샘플링 후 재생 (JackAudioHAL과 동일 파라미터) |

- `main.py:_build_tts()` — JackAudioHAL과 동일한 ES8388 device_idx를 `EspeakTts`에 전달

### 3-4. 테스트 업데이트
- `tests/test_tts.py` `TestEspeakTts` — Popen 기반 mock → `_start_thread` / `_kill_current` mock으로 교체

---

## 오늘 변경된 파일 목록

| 파일 | 변경 유형 | 내용 요약 |
|------|-----------|-----------|
| `main.py` | 수정 | BootSequence 연동, _build_tts() device_idx 공유 |
| `audio/boot_sequence.py` | 수정 | config import, 마지막 비프 후 대기 추가 |
| `audio/tts.py` | 재작성 | subprocess.run + sounddevice, 스레드 기반 비동기 |
| `vision/csi_camera_hal.py` | 수정 | _setup_isp_pipeline() 추가 |
| `config.py` | 수정 | CAMERA_OCCLUSION_CHANGE_THRESH 1.0, CSI 센서 상수 추가 |
| `tests/test_tts.py` | 수정 | EspeakTts 테스트 mock 방식 업데이트 |
| `checklist.md` | 신규 | 착용 테스트 체크리스트 |

