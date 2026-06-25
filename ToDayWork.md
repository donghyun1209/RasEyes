## Phase 4-D 진행 현황 (2026-06-25)

### 완료
- [x] Orange Pi 5에서 의존성 설치 — sounddevice, gpiod, VL53L1X pip 설치 완료
- [x] 카메라 / ToF 단품 테스트 — CSICameraHAL PASS, VL53L1XHAL PASS
- [x] `RASEYES_HW=1 python main.py` 통합 테스트 — ToF 실측으로 MID/HIGH 경보 정상 동작 확인
- [x] `scripts/test_device.py` 작성 — 단품 테스트 자동화 스크립트
- [x] `scripts/bench_rknn.py` 작성 — RKNN 추론 속도 측정 스크립트

### 오늘 발견 및 수정한 버그
- `sensor/vl53l1x_hal.py`:
  - `VL53L1X.VL53L1X._TOF_LIBRARY` → `VL53L1X._TOF_LIBRARY` (모듈 레벨 변수)
  - `i2c_port=` → `i2c_bus=` (설치된 라이브러리 파라미터명)
  - `start_ranging(0)` → `start_ranging(3)` LONG 연속 측정 모드
- `main.py`:
  - rknnlite2/ultralytics/PortAudio 미설치 시 `start()` 크래시 대신 graceful fallback 추가

### 추가 완료 (2026-06-25 오후)
- [x] systemd 서비스 등록 — `enabled`, `active (running)` 확인
- [x] 부팅 시간 — kernel 3.8s + userspace 7.2s = **11초** (KPI 45초 통과)
- [x] RKNN 모델 미설치 시 crash 버그 수정 — `_build_vision()`에서 파일 존재 확인 후 MockVision fallback

### 남은 작업

- [ ] `sudo apt install -y libportaudio2` — JackAudioHAL 오디오 출력 활성화
- [ ] `scripts/export_rknn.py`로 `yolov8n.rknn` 생성 후 scp 전송 (x86 Linux 환경 필요)
  ```bash
  scp yolov8n.rknn raseyes:~/RasEyes/
  ```
- [ ] RKNN 추론 속도 측정: `python3 scripts/bench_rknn.py` (목표 < 60ms)

### 참고 — 하드웨어 한계
- CSI 카메라 FPS: ~10 FPS (KPI 15 FPS 미달, `/dev/video11` V4L2 드라이버 한계)
- RKNN/torch 없으면 자동으로 MockVision fallback → ToF+오디오 파이프라인은 정상 동작
- rknnlite2 설치됨, ultralytics/libportaudio2 미설치 상태로 MockVision+MockAudio 모드 실행 중
