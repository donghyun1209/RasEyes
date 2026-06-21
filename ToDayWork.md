# 2026-06-21 작업 일지 — Phase 4: Orange Pi 5 하드웨어 이식

## 목표
PC Mock 환경(Phase 0~3)에서 검증된 코드베이스를 Orange Pi 5(RK3588S+NPU) 실 하드웨어에서 구동하기 위한 HAL 구현체 및 시스템 서비스 이식.

---

## 완료한 작업

### 4-A. 하드웨어 HAL 구현

#### `config.py` — Phase 4 상수 추가
```
CSI_DEVICE_PATH=/dev/video11, TOF_I2C_PORT=5, TOF_I2C_ADDRESS=0x29
TOF_TIMING_BUDGET_US=50000, TOF_INTER_MEASUREMENT_MS=50
RKNN_MODEL_PATH=yolov8n.rknn, GPIO_BUTTON_PIN=26
AUDIO_SAMPLE_RATE=44100, AUDIO_HIGH_FREQ_HZ=2000.0, AUDIO_MID_FREQ_HZ=1000.0
AUDIO_BEEP_DURATION_MS=80
```

#### `vision/csi_camera_hal.py` — OV13855 MIPI CSI 카메라 HAL
- `CSICameraHAL(BaseCameraHAL)`: `cv2.VideoCapture("/dev/video11")` 기반
- `CAP_PROP_BUFFERSIZE=1`로 프레임 지연 최소화, 해상도 불일치 시 소프트웨어 리사이즈 폴백

#### `sensor/vl53l1x_hal.py` — VL53L1X ToF 센서 HAL
- **핵심**: aarch64 64비트 ctypes 패치 적용 (함수 포인터 크기 오류 방지)
  - `lib.initialise.restype = c_void_p` 외 6개 argtypes/restype 명시
- `read_distance_cm()`: 0mm(범위 초과) → `TOF_OUT_OF_RANGE_CM`, 그 외 mm÷10 반환

#### `audio/jack_hal.py` — 3.5mm 이어폰 잭 오디오 HAL
- `sounddevice` + `numpy` 사인파 생성
- 10ms fade-in/fade-out 엔벨로프 적용(클릭 방지)
- HIGH(2000Hz) / MID(1000Hz) / NONE 알림 레벨 지원, `blocking=False` 비동기 출력

---

### 4-B. RKNN NPU 추론 파이프라인

#### `vision/rknn_detector.py` — RKNN NPU 추론 비전 모듈
- `VisionInterface` 완전 구현, `rknnlite2` lazy import (PC 수집 오류 없음)
- BGR→RGB 변환, 640×640 리사이즈 → `rknn.inference` → `cv2.dnn.NMSBoxes` NMS 후처리
- bbox 좌표를 원본 프레임 해상도로 역변환하여 `DetectionResult` 반환

#### `scripts/export_rknn.py` — PC 측 RKNN 모델 변환 스크립트
- YOLOv8n `.pt` → ONNX → RKNN INT8 양자화 변환
- `argparse` 기반 CLI (`--model`, `--output`, `--dataset`, `--no-quant`)
- `rknn-toolkit2` 필요 (PC 전용, Orange Pi 5 미설치)

---

### 4-C. 시스템 서비스 및 주변 기능

#### `raseyes.service` — systemd 자동 시작 서비스
- `Environment="RASEYES_HW=1"`, `Restart=on-failure`, `RestartSec=5`, `StartLimitBurst=5`
- 등록: `sudo cp raseyes.service /etc/systemd/system/ && sudo systemctl enable raseyes`

#### `audio/boot_sequence.py` — 부팅 완료 오디오 큐
- `BootSequence.play(audio_hal)`: MID(0.15s) → MID(0.15s) → HIGH 멜로디 시퀀스

#### `sensor/button_handler.py` — 물리 버튼 GPIO 핸들러
- `gpiod` lazy import, daemon 스레드 20ms 폴링, 50ms 디바운싱
- falling-edge 감지, `on_press: Callable[[], None]` 콜백 기반

#### `main.py` — 환경변수 기반 HAL factory 함수 추가
- `_build_vision()`, `_build_sensor()`, `_build_audio()` factory 함수
- `RASEYES_MOCK=1` → 전체 Mock, `RASEYES_HW=1` → 하드웨어 HAL, 기본 → PC 혼합 모드
- `_read_cpu_temp()`: HW 모드에서 `/sys/class/thermal/thermal_zone0/temp` 읽기

#### `logs/logger.py` + `logs/__init__.py` — CSV 운영 로거 (누락 파일 생성)
- pytest 수집 시 `ModuleNotFoundError: No module named 'logs'` 오류 발견 및 수정
- `CsvLogger`: `open()`, `write_row()`, `close()` 인터페이스, 헤더 자동 작성

#### `requirements-rpi.txt` — 의존성 추가
- `VL53L1X>=0.0.5`, `sounddevice>=0.4.6`, `gpiod>=2.0.0`, rknnlite2 주석 안내

---

## 발견된 버그 및 수정

| 문제 | 원인 | 수정 |
|------|------|------|
| `ModuleNotFoundError: No module named 'logs'` | `logs/` 디렉터리가 git에 미추가 | `logs/__init__.py`, `logs/logger.py` 생성 |
| pytest 미설치 | miniconda3 환경에 없음 | `pip install pytest pytest-cov` |

---

## 테스트 결과

```
72개 pytest 테스트 전체 통과 (PC Mock 모드)
```

---

## 다음 단계 (TODO)

- [ ] `scripts/export_rknn.py` 실행으로 `yolov8n.rknn` 생성 후 `scp`로 Orange Pi 5 전송
- [ ] Orange Pi 5에서 각 HAL 단품 테스트
  ```bash
  python -c "from vision.csi_camera_hal import CSICameraHAL; ..."
  python -c "from sensor.vl53l1x_hal import VL53L1XHAL; ..."
  python -c "from audio.jack_hal import JackAudioHAL; ..."
  ```
- [ ] `RASEYES_HW=1 python main.py` 통합 E2E 테스트 (FPS≥15, 비프음 확인)
- [ ] systemd 서비스 등록 및 부팅 자동 시작 검증
- [ ] RKNN 추론 속도 측정 (목표: 평균 <60ms)
