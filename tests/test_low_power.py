"""저전력(다이내믹 FPS) 모드 진입/해제 판정 테스트.

`main._should_low_power`가 근접 물체 부재로 비전 워커를 4 FPS로 낮출지 판단하는
로직을 검증한다. 2026-08-27 야외 실측에서 진입 51회/해제 50회(11.9분)의 진동이
관측됐고, 같은 원인이 둘러보기(360° 스캔)의 방향 누락까지 만들고 있었다.
물리 하드웨어 없이 동작한다.
"""
from typing import Tuple

import config
from main import _should_low_power


def _feed(
    distance_cm: float,
    times: int,
    *,
    ae_settled: bool = True,
    scan_active: bool = False,
    active: bool = False,
    streak: int = 0,
) -> Tuple[bool, int]:
    """같은 거리를 N회 연속 넣는다 (test_fps_fallback._feed_fps와 같은 방식)."""
    for _ in range(times):
        active, streak = _should_low_power(
            distance_cm, ae_settled, scan_active, active, streak
        )
    return active, streak


_FAR = config.DYNAMIC_FPS_NO_OBSTACLE_DIST_CM + 50.0   # 진입선 밖 (트인 공간)
_NEAR = config.MID_RISK_DIST_CM - 50.0                 # 해제선 안 (근접 물체)
_BAND = (config.MID_RISK_DIST_CM + config.DYNAMIC_FPS_NO_OBSTACLE_DIST_CM) / 2.0


class TestEnterDebounce:
    """진입에는 연속 프레임을 요구한다."""

    def test_single_far_frame_does_not_enter(self) -> None:
        """한 프레임만 비어 있다고 저전력에 들어가지 않는다."""
        active, streak = _feed(_FAR, 1)
        assert active is False
        assert streak == 1

    def test_one_short_of_debounce_does_not_enter(self) -> None:
        """디바운스 직전까지는 진입하지 않는다."""
        active, _ = _feed(_FAR, config.DYNAMIC_FPS_ENTER_DEBOUNCE_FRAMES - 1)
        assert active is False

    def test_debounce_frames_enter(self) -> None:
        """트인 공간이 연속으로 관측되면 정상적으로 진입한다 — 전력 절감이 회귀하면 안 된다."""
        active, streak = _feed(_FAR, config.DYNAMIC_FPS_ENTER_DEBOUNCE_FRAMES)
        assert active is True
        assert streak == 0

    def test_near_frame_resets_streak(self) -> None:
        """중간에 물체가 잡히면 카운터가 처음부터 다시 센다."""
        _, streak = _feed(_FAR, config.DYNAMIC_FPS_ENTER_DEBOUNCE_FRAMES - 1)
        active, streak = _should_low_power(_NEAR, True, False, False, streak)
        assert active is False
        assert streak == 0


class TestReleaseIsImmediate:
    """해제에는 디바운스를 걸지 않는다 (저전력 중 한 사이클이 250ms라 반응이 늦어진다)."""

    def test_single_near_frame_releases(self) -> None:
        """물체가 잡히면 한 프레임 만에 해제된다."""
        active, _ = _should_low_power(_NEAR, True, False, True, 0)
        assert active is False

    def test_release_does_not_wait_for_debounce(self) -> None:
        """해제가 디바운스를 태우면 근접 반응이 그만큼 늦어진다 — 회귀 방지."""
        active, _ = _feed(_NEAR, 1, active=True)
        assert active is False


class TestHysteresisBand:
    """진입선(200cm)과 해제선(150cm) 사이에서는 현 상태를 유지한다."""

    def test_band_holds_active(self) -> None:
        """저전력 중 밴드 안 거리로는 해제되지 않는다."""
        active, _ = _feed(_BAND, 5, active=True)
        assert active is True

    def test_band_does_not_enter(self) -> None:
        """평상시 밴드 안 거리로는 진입하지 않는다."""
        active, _ = _feed(_BAND, config.DYNAMIC_FPS_ENTER_DEBOUNCE_FRAMES * 2)
        assert active is False


class TestOscillationSuppressed:
    """2026-08-27 실측 진동(진입 51회/해제 50회, 11.9분)의 회귀 테스트."""

    def test_alternating_oor_and_valid_does_not_enter(self) -> None:
        """OoR 대체값과 유효 측정이 번갈아 나와도 저전력에 들어가지 않는다.

        RangeStatus 게이트가 무효 측정을 TOF_OUT_OF_RANGE_CM으로 바꾸면서 거리가
        400 ↔ 20~140cm를 오간다. 히스테리시스 밴드(150~200cm)를 매번 건너뛰므로
        디바운스가 없으면 사이클마다 진입/해제가 반복된다.
        """
        active, streak = False, 0
        for _ in range(20):
            active, streak = _should_low_power(
                config.TOF_OUT_OF_RANGE_CM, True, False, active, streak
            )
            assert active is False, "OoR 한 프레임만으로 진입하면 안 된다"
            active, streak = _should_low_power(40.0, True, False, active, streak)
            assert active is False

    def test_sustained_open_space_still_enters(self) -> None:
        """진동이 멎고 트인 상태가 이어지면 진입한다 (억제가 과하면 전력이 회귀한다)."""
        active, streak = _should_low_power(40.0, True, False, False, 0)
        active, streak = _feed(
            config.TOF_OUT_OF_RANGE_CM,
            config.DYNAMIC_FPS_ENTER_DEBOUNCE_FRAMES,
            active=active,
            streak=streak,
        )
        assert active is True


class TestScanBlocksLowPower:
    """둘러보기 중에는 저전력을 끈다 — 2026-08-27 규명된 발화 누락 ③의 원인."""

    def test_scan_prevents_entry(self) -> None:
        """스캔 중에는 아무리 트여 있어도 진입하지 않는다."""
        active, _ = _feed(
            _FAR, config.DYNAMIC_FPS_ENTER_DEBOUNCE_FRAMES * 2, scan_active=True
        )
        assert active is False

    def test_scan_releases_active_low_power(self) -> None:
        """저전력인 채로 스캔이 시작되면 즉시 해제한다.

        진입 차단만으로는 부족하다 — 스캔 시작 시점에 이미 4 FPS면 그 상태로
        회전이 시작되어 비전 데이터가 만료되고, ToF와 짝지을 탐지가 사라진다.
        """
        active, _ = _should_low_power(_FAR, True, True, True, 0)
        assert active is False

    def test_scan_resets_streak(self) -> None:
        """스캔이 끝난 뒤 진입하려면 디바운스를 처음부터 다시 채워야 한다."""
        _, streak = _should_low_power(_FAR, True, True, False, 5)
        assert streak == 0


class TestAeGate:
    """AE 수렴 중에는 진입을 미룬다 (기존 동작 보존)."""

    def test_unsettled_ae_blocks_entry(self) -> None:
        """AE가 조정 중이면 트인 공간이라도 진입하지 않는다."""
        active, _ = _feed(
            _FAR, config.DYNAMIC_FPS_ENTER_DEBOUNCE_FRAMES * 2, ae_settled=False
        )
        assert active is False

    def test_unsettled_ae_still_allows_release(self) -> None:
        """이미 저전력이면 AE 상태와 무관하게 근접 물체로 해제된다."""
        active, _ = _should_low_power(_NEAR, False, False, True, 0)
        assert active is False

    def test_unsettled_ae_does_not_force_release(self) -> None:
        """AE 미수렴만으로 저전력이 풀리지는 않는다 (거리 판정이 기준이다)."""
        active, _ = _should_low_power(_FAR, False, False, True, 0)
        assert active is True
