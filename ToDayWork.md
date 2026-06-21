# RasEyes 개발일지 — Phase 4: 드디어 Orange Pi 5에 올린다

> 2026-06-21

---

## 오늘 한 일 요약

지금까지 맥북에서 Mock 환경으로만 돌리던 RasEyes를 실제 Orange Pi 5 하드웨어에서 구동하기 위한 HAL 구현체들을 전부 작성했다. 코드 구조 자체는 안 건드리고, 하드웨어별 구현체만 갈아끼우는 방식

---

## 뭘 만들었냐면

### 카메라 — CSI 카메라 HAL

Orange Pi 5에 붙어 있는 OV13855 MIPI CSI 카메라를 OpenCV로 잡는 클래스

기존 `OpenCVCamera`랑 거의 똑같은데, 카메라 인덱스(0, 1...)가 아니라 장치 경로(`/dev/video11`)를 받는다는 점이 다르다. 버퍼 크기를 1로 설정해서 프레임 지연이 쌓이지 않게 했고, 해상도가 안 맞으면 소프트웨어로 리사이즈하는 폴백도 넣었다.

### ToF 센서 — VL53L1X HAL

VL53L1X 드라이버를 쓰는데, 64비트 ARM(aarch64)에서 ctypes 함수 포인터 크기가 틀려서 세그폴트가 나는 버그가 있다. 공식 이슈에도 올라온 known 버그라서, 라이브러리 내부 C 함수 시그니처를 직접 재정의해서 패치했다.

```python
lib = VL53L1X._TOF_LIBRARY
lib.initialise.restype = c_void_p
lib.getDistance.restype = c_uint16
# ... 나머지 argtypes도 명시
```

이거 안 하면 `start_ranging()` 호출 순간 프로세스가 죽는다.

거리 읽기는 0mm(측정 범위 초과)이면 `TOF_OUT_OF_RANGE_CM`을 반환하고, 그 외엔 mm을 10으로 나눠서 cm로 돌려준다.

### 오디오 — 이어폰 잭 HAL

`sounddevice` + `numpy`로 사인파를 만들어서 3.5mm 잭으로 출력한다. HIGH(2000Hz), MID(1000Hz) 두 가지 주파수를 쓰고, 시작/끝 10ms에 페이드인·페이드아웃을 걸어서 "딱" 하는 클릭 노이즈를 없앴다.

비동기(`blocking=False`)라서 비프음이 나오는 동안에도 메인 루프가 멈추지 않는다.

---

## RKNN NPU 파이프라인

Orange Pi 5에는 NPU가 달려 있어서 여기서 추론을 돌릴 수 있다. PC에서 YOLOv8n 모델을 INT8로 변환해서(`scripts/export_rknn.py`) Orange Pi 5로 보내면, `RknnDetector`가 rknnlite2로 로드해서 추론한다.

640×640으로 리사이즈 → NPU 추론 → NMS 후처리 → 원본 해상도 bbox 역변환 순서다.

rknnlite2 설치가 안 된 환경(또는 모델 파일이 없을 때)엔 자동으로 PyTorch CPU 추론으로 폴백되도록 factory 함수에서 처리해뒀다.

---

## 시스템 서비스

전원만 꽂으면 자동으로 실행되도록 systemd 서비스도 만들었다.

```ini
Environment="RASEYES_HW=1"
Restart=on-failure
RestartSec=5
```

`RASEYES_HW=1`을 환경변수로 넣어두면 main.py가 하드웨어 HAL을 선택한다.

부팅 완료 시엔 MID → MID → HIGH 멜로디가 울려서 "준비됐다"는 신호를 준다. 시각장애인이 화면 없이 상태를 인지할 수 있게.

버튼 핸들러도 추가했다. GPIO 핀 폴링 방식이고, 50ms 디바운싱을 걸어서 채터링을 잡는다. 콜백 기반이라 나중에 뮤트 토글이나 종료 신호 등 원하는 동작을 자유롭게 연결할 수 있다.

---

## 배포

코드를 GitHub에 push하고, Orange Pi 5에서 git clone으로 받았다.

```bash
ssh raseyes 'git clone https://github.com/donghyun1209/RasEyes.git ~/RasEyes'
```

---

## 다음에 할 것

- [ ] Orange Pi 5에서 의존성 설치 (`pip install -r requirements-rpi.txt`)
- [ ] 카메라 / ToF / 오디오 단품 테스트
- [ ] `RASEYES_HW=1 python main.py` 통합 테스트 (FPS≥15, 비프음 확인)
- [ ] `scripts/export_rknn.py`로 `yolov8n.rknn` 생성 후 scp로 전송
- [ ] RKNN 추론 속도 측정 (목표 < 60ms)
- [ ] systemd 서비스 등록 및 부팅 자동 시작 검증
