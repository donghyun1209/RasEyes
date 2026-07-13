# RasEyes 착용 테스트 체크리스트

> 최종 업데이트: 2026-07-06  
> 목표 KPI: E2E < 500ms · FPS ≥ 15 · Recall > 95% · 오탐지 < 1회/분

---

## 시작 전 확인

- [ ] Orange Pi 전원 인가 후 이어폰 꽂기
- [ ] 부팅 완료 신호: **삐~비~빅 + "RasEyes ready"** 음성 들리는지
- [ ] 카메라 방향: 가슴~머리 높이 정면을 커버하는지

---

## A. 오디오 기본 동작

| # | 확인 항목 | 기대 결과 | 결과 |
|---|-----------|-----------|------|
| A-1 | 부팅 멜로디 (MID→MID→HIGH 비프) | 이어폰에서 삐~비~빅 들림 | |
| A-2 | "RasEyes ready" 음성 | 멜로디 직후 영어 음성 들림 | |
| A-3 | HIGH 경보음 (2000Hz) | 100cm 이내 장애물 시 빠른 비프 | |
| A-4 | MID 경보음 (1000Hz) | 150cm 이내 장애물 시 느린 비프 | |
| A-5 | TTS 음성 — HIGH | `"Danger! person, 80 centimeters, ahead"` 형식 | |
| A-6 | TTS 음성 — MID | `"chair on the left"` 형식 | |
| A-7 | TTS 음성 — ToF 단독 HIGH | `"Danger! Obstacle ahead"` | |
| A-8 | TTS 음성 — ToF 단독 MID | `"Caution, obstacle"` | |

---

## B. 방향 인식 정확도

| # | 테스트 조건 | 기대 TTS | 결과 |
|---|-------------|----------|------|
| B-1 | 장애물이 정면 (카메라 중앙) | `"person ahead"` | |
| B-2 | 장애물이 왼쪽 (bbox 중심 x < 33%) | `"person on the left"` | |
| B-3 | 장애물이 오른쪽 (bbox 중심 x > 66%) | `"person on the right"` | |

---

## C. 거리 임계값 동작

| # | 테스트 조건 | 기대 결과 | 결과 |
|---|-------------|-----------|------|
| C-1 | 사람이 정면에서 150cm 거리 | MID 비프 + TTS 발화 시작 | |
| C-2 | 사람이 100cm 이내 진입 | HIGH 비프로 전환 + HIGH TTS | |
| C-3 | 사람이 물러나 150cm 초과 | 경보 중단 (NONE) | |
| C-4 | 의자·테이블 정지 장애물 100cm | HIGH 비프 + TTS 정상 | |

---

## D. 특수 상황 / 엣지 케이스

| # | 테스트 조건 | 기대 결과 | 결과 |
|---|-------------|-----------|------|
| D-1 | 어두운 방 (Confidence < 0.4) | ToF 단독 모드 전환, TTS `"Danger! Obstacle ahead"` | |
| D-2 | 장애물 없는 정지 상태 | 경보 없음 (오탐지 없는지) | |
| D-3 | 문틀 통과 | 통과 중 경보 없거나 1회 이내 | |
| D-4 | 카메라 손으로 가림 | 짧은 3연속 비프 (삑삑삑) | |
| D-5 | TTS 쿨다운 — 동일 위험 연속 | 2초(HIGH) / 4초(MID) 이내 재발화 없음 | |
| D-6 | HIGH 발화 중 새 HIGH 진입 | 기존 TTS 중단 후 새 TTS 즉시 | |

---

## E. 성능 지표 (CSV 로그 확인)

> 테스트 후 `logs/raseyes_log.csv` 참조

| 지표 | KPI | 확인 방법 | 결과 |
|------|-----|-----------|------|
| FPS | ≥ 15 | `fps` 컬럼 평균 | |
| E2E 레이턴시 | < 500ms | `latency_ms` 컬럼 최대값 | |
| 오탐지 횟수 | < 1회/분 | `alert_triggered=True` & 실제 장애물 없던 경우 | |
| CPU 온도 | < 80°C | `cpu_temp` 컬럼 최대값 | |

---

## F. 안정성

| # | 테스트 조건 | 기대 결과 | 결과 |
|---|-------------|-----------|------|
| F-1 | 10분 이상 연속 착용 | 크래시·프리즈 없음 | |
| F-2 | 실외 직사광선 | 열 스로틀링(80°C) 미발동 (팬 상시 구동) | |
| F-3 | 재부팅 후 자동 시작 | 서비스 자동 기동 + 부팅 멜로디 | |

---

  ### 1. TTS로 안내되는 주요 물체 (26종)

  YOLOv8 COCO 클래스 중 fusion 대상 라벨입니다. 영어 TTS 전환 이후 번역 없이 COCO 영문 명칭 그대로 발화됩니다 (예: "person", "chair").

  • 보행/이동체:  person,  bicycle,  car,  motorcycle,  bus,  truck
  • 안전/안내 시설:  traffic light,  fire hydrant,  stop sign,  bench
  • 반려동물/동물:  cat,  dog,  bird
  • 휴대물품:  backpack,  umbrella,  handbag,  suitcase
  • 식기/가구/가전:  bottle,  cup,  chair,  couch,  dining table,  toilet,  tv,  laptop
  • 건물 구성:  door
