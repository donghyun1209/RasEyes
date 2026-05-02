# CLAUDE.md — RasEyes Project

## 1. Project Overview
* **RasEyes:** 시각장애인용 상단 사각지대(가슴~머리 높이) 장애물 탐지 웨어러블 엣지 AI 디바이스.
* **Target HW:** Raspberry Pi 5, Camera Module 3, VL53L1X(ToF), 블루투스 골전도 이어폰.
* **Current Phase:** PC 환경(macOS M5, Python 3.11, venv)에서의 모킹(Mocking) 기반 선행 개발 및 검증.
* **Constraints:** 100% On-device, 외부 API/Cloud 사용 불가.
* **KPIs:** End-to-End Latency < 500ms, 추론 < 60ms(15+ FPS), 탐지 Recall > 95%, 오탐지 < 1회/분.

## 2. Project Structure & Rules
* `/vision`, `/sensor`, `/fusion`, `/audio` 등 도메인별 폴더 분리. `main.py`는 오케스트레이션만 담당.
* 입력(비전, 센서)과 출력(오디오)은 반드시 추상화 계층(HAL) 인터페이스를 적용하여, 현재의 PC 모킹 클래스와 추후 RPi 하드웨어 제어 클래스를 쉽게 교체할 수 있도록 구현.
* 상수 및 임계값은 매직 넘버 대신 `config.py`에 분리.
* 타입 힌트와 구글 스타일 Docstring 필수 작성. 예외 처리 철저.

## 3. Core Logic (Sensor Fusion)
* **High Risk:** 객체 인식됨 & ToF 거리 <= 100cm & Confidence >= MIN_CONF. (즉각 경고 트리거)
* **Mid Risk:** 객체 인식됨 & ToF 거리 <= 150cm. (주의 경고 트리거)
* **Low-light Fallback:** 비전 모델의 Confidence가 0.4 미만이면 ToF 단독 모드로 전환.
* ToF 센서 값은 노이즈 제거를 위해 이동 평균 필터(window=3) 적용.

## 4. Development & Testing
* 비전 AI 환경: Apple Silicon MPS 가속 최우선 활용 (`torch.device("mps")`).
* 테스트: `pytest` 프레임워크 사용. (핵심 케이스: 거리 임계값 경계 조건, Fallback 전환 로직, 모킹 객체를 활용한 오탐지 테스트).
* 로깅: 로컬 CSV 파일에 1초 1회 기록 (timestamp, cpu_temp, fps, tof_distance_cm, alert_triggered).