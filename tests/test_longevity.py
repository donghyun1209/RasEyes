"""Longevity (장기 안정성) 테스트.

RasEyesApp 워커 스레드를 짧은 시간 동작시켜 메모리 누수가 없음을 검증한다.
실제 검증 시에는 RUN_DURATION_SEC를 600~1200으로 늘려 10~20분 이상 실행을 권장한다.
"""
import time
import tracemalloc

from main import RasEyesApp

RUN_DURATION_SEC: float = 5.0
MEMORY_GROWTH_LIMIT_BYTES: int = 4 * 1024 * 1024  # 4 MiB (tracemalloc 오버헤드·스레드 초기화 포함)


def test_longevity_no_memory_leak() -> None:
    """Mock 모드로 RasEyesApp 워커를 5초 동안 실행해 메모리 누수 없음을 검증한다."""
    app = RasEyesApp(use_mock=True)
    app.start()
    time.sleep(0.3)  # 스레드 워밍업

    tracemalloc.start()
    snapshot_before = tracemalloc.take_snapshot()

    time.sleep(RUN_DURATION_SEC)

    snapshot_after = tracemalloc.take_snapshot()
    tracemalloc.stop()
    app.stop()

    stats = snapshot_after.compare_to(snapshot_before, "lineno")
    growth = sum(s.size_diff for s in stats if s.size_diff > 0)
    assert growth < MEMORY_GROWTH_LIMIT_BYTES, (
        f"메모리 증가량({growth / 1024:.1f} KiB)이 "
        f"허용 한도({MEMORY_GROWTH_LIMIT_BYTES // 1024} KiB)를 초과"
    )
