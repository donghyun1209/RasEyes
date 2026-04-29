# Product Requirements Document (PRD): RasEyes

## 0. Context & Core Input (Summary)
* **Product Name:** RasEyes (라즈아이즈)
* **Target Audience:** 흰지팡이에 의존하여 보행하는 시각장애인 (Visually Impaired Persons)
* **The Problem:** 흰지팡이는 바닥의 장애물만 탐지할 수 있어, 가슴이나 머리 높이에 위치한 돌출된 장애물(간판, 나뭇가지, 트럭 적재함 등)에 의한 충돌 사고 위험이 매우 높음.
* **Key Hypothesis:** 카메라 비전 AI와 ToF(거리 측정) 센서를 결합한 웨어러블 엣지 디바이스를 통해 직관적인 청각적 피드백을 제공하면, 상체 타격 사고를 획기적으로 줄이고 보행 자신감을 높일 수 있다.
* **Data Source/Integrations:** 실시간 카메라 비전 스트림, 로컬 ToF 센서 데이터 (외부 API 종속성 없음, 100% On-device 처리).

---

## 1. Executive Summary & Context (The 'Why')
시각장애인의 독립적인 보행은 삶의 질과 직결되는 문제입니다. 기존의 보조 공학 기기들은 수백만 원에 달하거나, 스마트폰을 손에 들고 조작해야 하는 등 실사용성이 떨어집니다. RasEyes는 합리적인 가격의 상용 임베디드 보드(Raspberry Pi 5)와 엣지 AI(Edge AI)를 결합하여, 사용자의 두 손을 자유롭게 하고 상단 사각지대를 해소하는 '웨어러블 보행 보조 시스템'의 Zero to One(PoC) 모델입니다. 이 프로젝트는 단순한 기술적 과시가 아니라, 생존과 직결된 물리적 문제를 On-device AI로 해결하는 실질적인 엔지니어링 챌린지입니다.

## 2. Goals & Guardrail Metrics

### Core KPIs (Success Metrics)
* **System Latency (지연 시간):** 장애물 인식부터 음성 피드백 발생까지 **< 500ms** (보행 속도를 고려할 때 이 이상 지연되면 충돌함).
* **Frame Rate:** 비전 모델 추론 속도 **최소 15 FPS** 유지 (이동 시 화면 끊김 현상 방지).
* **Detection Accuracy:** 2m 이내 전방 장애물(가슴~머리 높이)에 대한 **Recall(재현율) > 95%** (위험을 놓치면 안 됨).

### Counter-metrics (Guardrail)
* **False Positive Rate (오탐지율):** 위험하지 않은 상황에서의 알림 빈도. 과도한 알림은 사용자의 청각 피로도를 높이고 기기 신뢰성을 훼손함 (1분당 오탐지 1회 미만 목표).
* **Thermal Throttling Rate:** 야외 환경 구동 시 CPU 온도가 80°C를 넘어 성능이 강제로 저하되는 시간의 비율 (전체 사용 시간의 5% 미만이어야 함).

## 3. User Stories with Acceptance Criteria (AC)

**Story 1: 시스템 부팅 및 준비 상태 확인**
* **As a** 시각장애인 사용자는,
* **I want to** 물리 버튼을 한 번 눌러 기기를 켜고,
* **So that** 화면 없이도 시스템이 정상 작동할 준비가 되었는지 알 수 있다.
* **AC 1:** 전원 인가 후 OS 부팅 및 AI 모델 로딩이 완료되면 골전도 이어폰을 통해 "RasEyes가 준비되었습니다"라는 오디오 피드백이 출력되어야 한다. (부팅 시간 < 45초)
* **AC 2:** 시스템 에러 발생 시(카메라 연결 불량 등), 다른 비프음(예: 삐삐삐)을 출력하여 에러 상태를 알려야 한다.

**Story 2: 장애물 접근 경고**
* **As a** 보행 중인 사용자는,
* **I want to** 전방 1.5m 이내에 장애물이 감지되었을 때 즉각적인 경고음을 듣고,
* **So that** 충돌을 피하기 위해 멈추거나 방향을 틀 수 있다.
* **AC 1:** 카메라 비전 혹은 ToF 센서가 1.5m 이내의 객체를 인식하면 즉시 경고음이 발생해야 한다.
* **AC 2:** 객체와의 거리가 가까워질수록(1.5m -> 1.0m -> 0.5m) 경고음의 주기(Frequency)가 빨라져야 한다.

## 4. Functional & Data Requirements

### Logic & Rules
* **Sensor Fusion Logic:** 카메라 비전 모델(YOLO)은 '무엇'인지 파악하고 바운딩 박스를 치지만 거리 추정에 약함. 반면 ToF 센서는 '거리'는 정확하지만 물체의 크기/종류는 모름.
    * *Rule:* 비전 모델이 객체를 탐지하고 해당 영역 중심점의 거리를 ToF 센서 값으로 맵핑하여 두 데이터가 모두 임계값(Threshold, ex. 1.5m) 이내일 때만 'High Risk' 알람을 트리거한다.

### MoSCoW Prioritization
* **Must Have (PoC 핵심):**
    * TFLite 기반 경량화 객체 인식 모델 실시간 구동
    * 카메라와 ToF 센서 데이터 동기화
    * 거리에 따른 다단계 오디오(비프음) 피드백
    * Headless 부팅 및 물리 스위치 제어
* **Should Have:**
    * 배터리 잔량 부족(20% 미만) 시 오디오 경고
    * 발열 제어를 위한 액티브 쿨러 자동 PWM 제어 스크립트
* **Could Have:**
    * "사람입니다", "기둥입니다" 등 객체 클래스(Class)에 따른 음성 안내 (TTS)
* **Won't Have (이번 페이즈에서는 제외):**
    * 클라우드 서버 연동 및 데이터 업로드 (오프라인 구동 원칙)
    * GUI 대시보드 또는 모바일 컴패니언 앱 연동

## 5. User Experience & Edge Cases

### Happy Path
1. 외출 전, 체스트 스트랩을 가슴에 착용하고 배터리를 연결한다.
2. 기기 측면의 푸시 버튼을 누른다.
3. 약 30초 후 골전도 이어폰에서 "띠링- 시스템 준비 완료" 소리가 들린다.
4. 보행을 시작한다.
5. 전방 2m 앞 간판을 향해 걸어간다. 1.5m 지점에서 "뚜- 뚜-", 1m 지점에서 "뚜뚜뚜-" 소리가 들린다.
6. 방향을 틀어 장애물을 벗어나면 소리가 멈춘다.

### Edge Cases
* **센서가 가려지거나 오염된 경우 (Sensor Blindness):** 비전 프레임의 전체 픽셀 변화가 특정 임계값 이하로 지속되면 "카메라를 확인해주세요"라는 알림 발생.
* **극심한 역광 또는 야간 (Low Light):** 비전 모델의 Confidence Score가 떨어지면, ToF 센서 단독 모드로 Fallback하여 거리 기반 기본 경고만 수행.
* **블루투스 이어폰 연결 끊김:** 이어폰 연결이 끊기면 라즈베리파이 보드에 부착된 소형 부저(Buzzer)에서 비상음이 울려 사용자가 인지하도록 함.

## 6. Technical Considerations

* **Hardware Specs:** Raspberry Pi 5 (8GB), NVMe SSD 128GB (PCIe HAT), Camera Module 3, VL53L1X(ToF), Bluetooth Earphones.
* **AI Model:** YOLOv8 Nano 또는 MobileNet SSD. (TFLite 포맷으로 변환하여 NPU/CPU 가속 활용).
* **Data Schema (Local Log용):** 디버깅을 위해 로컬 `.csv` 파일에 초당 1회 상태를 기록.
    * `timestamp`, `cpu_temp`, `fps`, `object_detected(bool)`, `closest_distance(cm)`
* **System Architecture:** Linux OS 기반, `systemd`를 활용한 데몬(Daemon) 서비스 등록. 전원이 켜지면 파이썬 메인 스크립트가 자동 실행되도록 구성.

## 7. Go-To-Market (GTM) & Analytics

* **GTM Strategy (Beta Test):**
    * 타겟: 시각장애인 복지관 또는 관련 커뮤니티의 지인 1~2명을 통한 안전한 통제 환경 내 실증 테스트 (공원, 넓은 실내 등).
    * 목표: 하드웨어 착용감 및 오디오 피드백의 직관성 인터뷰.
* **Analytics & Tracking (Offline):**
    * 통신 모듈이 없으므로, 테스트 종료 후 NVMe SSD에 저장된 CSV 로그 파일을 PC로 추출하여 분석.
    * 중점 분석 지표: FPS 방어율, 평균 CPU 온도, 위험 알람 트리거 빈도.

## 8. Open Questions & Risks

* **Risk 1 (Thermal):** 한여름(7~8월) 야외 땡볕에서 액티브 쿨러만으로 라즈베리파이 5의 발열을 잡아내고 스로틀링을 방지할 수 있을 것인가? (케이스 통풍구 설계 최적화 필요)
* **Risk 2 (Audio UX):** 도심의 심한 소음 속에서 골전도 이어폰의 알람이 명확하게 들릴 것인가?
* **Open Question:** '무엇'인지 말해주는 것(TTS)이 유용한가, 아니면 뇌의 인지 부하를 줄이기 위해 단순히 '비프음'만 내는 것이 더 안전한가? (사용자 테스트를 통해 결정해야 함).
