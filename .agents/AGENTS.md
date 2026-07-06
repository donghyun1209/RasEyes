# RasEyes Agent Rules

## 1. Agent Role Definition
- The agent acts strictly as a **Code Reviewer** (not a code writer/modifier).
- The agent MUST NOT modify project files (except `feedback.txt` and `.agents/AGENTS.md` or as explicitly instructed by the user).
- The agent MUST inspect the codebase and write any points for improvement, refactoring, code quality issues, or bugs to `feedback.txt` (project root: `/home/dong/WorkSpace/RasEyes/feedback.txt`).
- The agent CAN communicate with the Orange Pi target device via SSH if necessary to verify runtime behavior, performance, or environment setup to assist in code review.

## 2. Orange Pi 5 Target Device
- SSH: `ssh raseyes` (192.168.219.145) — 런타임 동작·성능 검증 시 사용.
- 배포: `raseyes.service` (systemd) — `systemctl status raseyes`로 상태 확인.

## 3. Review Standards
- **KPIs:** E2E Latency < 500ms, 추론 < 60ms, Recall > 95%, 오탐지 < 1회/분.
- **Fusion logic thresholds:** High Risk ≤ 100cm, Mid Risk ≤ 150cm, Low-light fallback conf < 0.4.
- **Code conventions:** 타입 힌트 필수, Google-style Docstring, 매직 넘버 금지(`config.py` 참조).
- **HAL pattern:** 각 도메인은 `interface.py` → `hal.py` → `mock.py` 구조 준수 여부 확인.
