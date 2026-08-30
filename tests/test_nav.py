"""v2.2 Phase 3 — BLE 길안내 발화 통합 테스트.

Phase 3이 "완료"로 표시된 뒤의 점검에서 결함 두 건이 드러났다. 둘 다 회귀
테스트가 하나도 없어서 놓친 것이라, 그 두 가지를 이 파일의 중심에 둔다.

1. **모르는 경로 코드를 'Proceed'(사실상 직진)로 발화했다.** 2.2 로드맵 Phase 1이
   "모르는 코드를 '직진'으로 몰아넣으면 회전을 놓치고도 조용히 지나간다"며
   명시적으로 금지한 패턴이다. 앱은 원칙대로 `?`로 남겨 보내는데 Pi가 뭉갰다.
2. **MID 장애물 경보가 길안내 발화에 막혀 스킵됐다.** AlertPolicy는 엣지 트리거고
   ALERT_REMINDER_SEC이 inf(재알림 꺼짐)라, 한 번 스킵된 경보는 래치가 풀려
   재진입할 때까지 다시 나오지 않는다 — 경고가 통째로 유실된다.

물리 하드웨어 없이 동작한다.
"""
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from audio.tts import EspeakTts
from fusion.engine import RiskLevel
from fusion.nav_parser import _TURN_MAPPING, parse_nav_instruction
from main import _decide_nav_speech
from tests.test_tts import _make_piper_tts

_IOS_ROUTE_PROVIDER = (
    Path(__file__).resolve().parent.parent
    / "ios" / "RasEyesApp" / "RasEyesApp" / "RouteProvider.swift"
)

# 두 TTS 구현은 선점 정책이 동일해야 한다 (CLAUDE.md §7).
_TTS_FACTORIES = [
    pytest.param(EspeakTts, id="espeak"),
    pytest.param(_make_piper_tts, id="piper"),
]


# ── 코드 → 문장 변환 ──────────────────────────────────────────────────────────

class TestParseNavInstruction:
    """정상 경로 — 앱의 압축 코드를 영어 문장으로 조립한다."""

    def test_known_code_with_distance(self) -> None:
        assert parse_nav_instruction("R|50") == "Turn right in 50 meters"

    def test_multi_character_code(self) -> None:
        """'X10'처럼 두 글자 이상인 코드가 잘려선 안 된다."""
        assert parse_nav_instruction("X10|1500").startswith("Crosswalk at 10 o'clock")

    def test_zero_distance_speaks_action_only(self) -> None:
        assert parse_nav_instruction("S|0") == "Start"

    def test_missing_distance_speaks_action_only(self) -> None:
        assert parse_nav_instruction("A") == "Arrive at destination"

    def test_empty_code(self) -> None:
        assert parse_nav_instruction("") == ""

    def test_non_numeric_distance_is_dropped(self) -> None:
        """BLE는 외부 입력이라 형식이 깨질 수 있다 — 'in abc meters'는 말하지 않는다."""
        assert parse_nav_instruction("R|abc") == "Turn right"


class TestUnknownCodeIsNotStraight:
    """⚠ 핵심 회귀 — 모르는 코드를 직진으로 뭉개면 회전을 놓치고 조용히 지나간다."""

    def test_unknown_code_is_not_proceed(self) -> None:
        phrase = parse_nav_instruction("?|50")
        assert "Proceed" not in phrase
        assert "straight" not in phrase.lower()

    def test_unknown_code_warns_with_distance(self) -> None:
        """'내가 직접 판단해야 한다'가 전달돼야 한다 — 거리는 그대로 알려준다."""
        phrase = parse_nav_instruction("?|50")
        assert "unknown" in phrase.lower()
        assert "50 meters" in phrase

    def test_unmapped_code_also_warns(self) -> None:
        """앱이 코드를 새로 늘렸는데 Pi가 못 따라간 경우도 같은 취급이어야 한다."""
        assert "unknown" in parse_nav_instruction("ZZ|10").lower()


class TestIosContract:
    """앱이 보내는 코드를 Pi가 전부 아는지 — 접합부가 조용히 어긋나는 것을 막는다."""

    def test_every_ios_maneuver_code_is_mapped(self) -> None:
        if not _IOS_ROUTE_PROVIDER.exists():
            pytest.skip("ios/ 소스 없음 (Pi 배포본은 ios/를 제외한다 — CLAUDE.md §8)")
        source = _IOS_ROUTE_PROVIDER.read_text(encoding="utf-8")
        codes = set(re.findall(r'case\s+\w+\s*=\s*"([^"]+)"', source))
        assert codes, "ManeuverCode rawValue를 하나도 못 찾았다 — 정규식 점검 필요"
        # '?'는 앱이 '모르는 코드'를 표시하는 값이라 매핑하지 않는 것이 맞다.
        missing = codes - {"?"} - set(_TURN_MAPPING)
        assert not missing, f"Pi 파서에 없는 앱 코드: {sorted(missing)}"


# ── 발화 중재 (_decide_nav_speech) ────────────────────────────────────────────

class TestNavSpeechArbitration:
    """길안내를 언제 내보낼지 — 로드맵 Phase 3-2/3-3의 규칙."""

    def test_speaks_when_quiet(self) -> None:
        pending, playing, speak = _decide_nav_speech("R|50", None, None, False, False)
        assert speak == "R|50"
        assert playing == "R|50"
        assert pending is None

    def test_waits_while_tts_busy(self) -> None:
        pending, _, speak = _decide_nav_speech("R|50", None, None, True, False)
        assert speak is None
        assert pending == "R|50"

    def test_clears_playing_when_finished(self) -> None:
        pending, playing, speak = _decide_nav_speech(None, "R|50", None, False, False)
        assert (pending, playing, speak) == (None, None, None)

    def test_nothing_pending_is_noop(self) -> None:
        assert _decide_nav_speech(None, None, None, False, False) == (None, None, None)

    def test_high_alert_blocks_and_requeues(self) -> None:
        pending, playing, speak = _decide_nav_speech(
            None, "R|50", RiskLevel.HIGH, True, False
        )
        assert speak is None
        assert playing is None
        assert pending == "R|50", "선점당한 길안내가 유실되면 안 된다"

    def test_mid_alert_also_requeues(self) -> None:
        """⚠ 핵심 회귀 — 예전엔 HIGH만 원복 대상이라 MID 때 길안내가 사라졌다."""
        pending, playing, _ = _decide_nav_speech(
            None, "R|50", RiskLevel.MID, True, False
        )
        assert playing is None
        assert pending == "R|50"

    def test_alert_cycle_does_not_speak_nav(self) -> None:
        """장애물 경보가 나간 사이클에는 길안내를 겹쳐 말하지 않는다."""
        _, _, speak = _decide_nav_speech("R|50", None, RiskLevel.MID, False, False)
        assert speak is None

    def test_scan_holds_nav(self) -> None:
        """둘러보기 중에는 길안내를 보류한다 (로드맵 Phase 3-2)."""
        pending, _, speak = _decide_nav_speech("R|50", None, None, False, True)
        assert speak is None
        assert pending == "R|50"

    def test_scan_requeues_playing(self) -> None:
        pending, playing, _ = _decide_nav_speech(None, "R|50", None, True, True)
        assert playing is None
        assert pending == "R|50"

    def test_newer_instruction_wins_on_preemption(self) -> None:
        """로드맵 3항 — 오래된 지시는 비우고 최신으로 덮어쓴다.

        선점당한 지시를 무조건 되돌리면, 그 사이 도착한 새 지시를 덮어써
        요구사항을 정확히 반대로 어긴다.
        """
        pending, playing, _ = _decide_nav_speech(
            "L|10", "R|50", RiskLevel.HIGH, True, False
        )
        assert pending == "L|10"
        assert playing is None


# ── 장애물 경보의 길안내 선점 (TTS 계층) ──────────────────────────────────────

def _busy_with(tts, risk: RiskLevel) -> None:
    """발화 중 상태를 흉내낸다 — 합성 스레드가 살아 있고 그 위험 수준이 risk."""
    thread = MagicMock()
    thread.is_alive.return_value = True
    tts._thread = thread
    tts._current_risk = risk
    tts._last_mid_time = 0.0
    tts._last_high_time = 0.0


class TestObstacleAlertPreemptsNav:
    """⚠ 핵심 회귀 — 길안내 합성 중 MID 경보가 스킵돼 통째로 사라졌다."""

    @pytest.mark.parametrize("make", _TTS_FACTORIES)
    def test_mid_preempts_nav(self, make) -> None:
        tts = make()
        _busy_with(tts, RiskLevel.NAV)
        with patch.object(tts, "_kill_current") as kill, \
             patch.object(tts, "_start_thread") as start:
            tts.speak("Danger ahead", RiskLevel.MID)
            kill.assert_called_once()
            start.assert_called_once()

    @pytest.mark.parametrize("make", _TTS_FACTORIES)
    def test_mid_still_skips_during_obstacle_speech(self, make) -> None:
        """장애물끼리의 기존 동작은 그대로 — 경보를 겹쳐 말하지 않는다."""
        tts = make()
        _busy_with(tts, RiskLevel.HIGH)
        with patch.object(tts, "_start_thread") as start:
            tts.speak("Danger ahead", RiskLevel.MID)
            start.assert_not_called()

    @pytest.mark.parametrize("make", _TTS_FACTORIES)
    def test_nav_never_preempts(self, make) -> None:
        """반대 방향은 성립하지 않는다 — 길안내가 경보를 끊으면 안 된다."""
        tts = make()
        _busy_with(tts, RiskLevel.MID)
        with patch.object(tts, "_start_thread") as start:
            tts.speak("Turn right in 50 meters", RiskLevel.NAV)
            start.assert_not_called()

    @pytest.mark.parametrize("make", _TTS_FACTORIES)
    def test_start_thread_records_risk(self, make) -> None:
        """선점 판단의 근거이므로 발화 시작 시 반드시 기록돼야 한다."""
        tts = make()
        with patch.object(tts, "_speak_worker"):
            tts.speak("Turn right in 50 meters", RiskLevel.NAV)
            assert tts._current_risk is RiskLevel.NAV
            if tts._thread is not None:
                tts._thread.join(timeout=1.0)
