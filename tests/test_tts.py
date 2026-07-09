"""Phase 7 TTS 통합 테스트."""
import sys
import threading
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import config
from audio.mock_tts import MockTts
from audio.piper_tts import PiperTts
from audio.prerendered_tts import load_prerendered_cache, phrase_to_filename
from audio.tts import EspeakTts
from fusion.engine import FusionEngine, FusionResult, RiskLevel
from main import _build_tts_text
from vision.interface import DetectionResult


# ── 공통 헬퍼 ────────────────────────────────────────────────────────────────

def _det_at(label: str, x1: int, x2: int, conf: float = 0.9) -> DetectionResult:
    """방향 테스트 전용: 가로 위치가 다른 bbox 생성 (y는 고정)."""
    return DetectionResult(label=label, confidence=conf, bbox=(x1, 0, x2, 100))


# ── FusionEngine 방향 계산 테스트 ─────────────────────────────────────────────

class TestDirection:
    """bbox 중심 x 기반 방향 분류 검증."""

    def test_direction_left(self) -> None:
        engine = FusionEngine()
        # center_x=50, ratio=0.078 < 0.33 → 왼쪽
        result = engine.evaluate([_det_at("person", 0, 100)], raw_distance_cm=80.0)
        assert result.direction == "왼쪽"

    def test_direction_center(self) -> None:
        engine = FusionEngine()
        # center_x=320, ratio=0.5 → 정면
        result = engine.evaluate([_det_at("person", 200, 440)], raw_distance_cm=80.0)
        assert result.direction == "정면"

    def test_direction_right(self) -> None:
        engine = FusionEngine()
        # center_x=545, ratio=0.852 > 0.66 → 오른쪽
        result = engine.evaluate([_det_at("person", 450, 640)], raw_distance_cm=80.0)
        assert result.direction == "오른쪽"

    def test_direction_left_boundary(self) -> None:
        engine = FusionEngine()
        # center_x=200, ratio=0.3125 < 0.33 → 왼쪽
        result = engine.evaluate([_det_at("person", 100, 300)], raw_distance_cm=80.0)
        assert result.direction == "왼쪽"

    def test_direction_just_above_left_boundary(self) -> None:
        engine = FusionEngine()
        # center_x=213, ratio=0.3328 > 0.33 → 정면
        result = engine.evaluate([_det_at("person", 113, 313)], raw_distance_cm=80.0)
        assert result.direction == "정면"

    def test_direction_just_below_right_boundary(self) -> None:
        engine = FusionEngine()
        # center_x=422, ratio=0.6594 < 0.66 → 정면
        result = engine.evaluate([_det_at("person", 322, 522)], raw_distance_cm=80.0)
        assert result.direction == "정면"

    def test_direction_just_above_right_boundary(self) -> None:
        engine = FusionEngine()
        # center_x=423, ratio=0.6609 > 0.66 → 오른쪽
        result = engine.evaluate([_det_at("person", 323, 523)], raw_distance_cm=80.0)
        assert result.direction == "오른쪽"

    def test_top_label_set(self) -> None:
        engine = FusionEngine()
        result = engine.evaluate([_det_at("chair", 0, 100)], raw_distance_cm=80.0)
        assert result.top_label == "chair"

    def test_tof_only_has_no_direction(self) -> None:
        engine = FusionEngine()
        # conf=0.1 < MIN_CONFIDENCE → tof_only_mode
        result = engine.evaluate([_det_at("person", 0, 100, conf=0.1)], raw_distance_cm=80.0)
        assert result.tof_only_mode is True
        assert result.direction is None
        assert result.top_label is None

    def test_none_risk_has_no_direction(self) -> None:
        engine = FusionEngine()
        result = engine.evaluate([], raw_distance_cm=200.0)
        assert result.risk_level == RiskLevel.NONE
        assert result.direction is None
        assert result.top_label is None

    def test_highest_confidence_selected(self) -> None:
        engine = FusionEngine()
        dets = [
            _det_at("chair", 0, 100, conf=0.5),    # 왼쪽
            _det_at("person", 200, 440, conf=0.9),  # 정면
        ]
        result = engine.evaluate(dets, raw_distance_cm=80.0)
        assert result.top_label == "person"
        assert result.direction == "정면"


# ── MockTts 테스트 ────────────────────────────────────────────────────────────

class TestMockTts:
    def test_initial_state(self) -> None:
        tts = MockTts()
        assert tts.last_spoken is None

    def test_speak_updates_last_spoken(self) -> None:
        tts = MockTts()
        tts.speak("위험, 정면 80센티 사람", RiskLevel.HIGH)
        assert tts.last_spoken == "위험, 정면 80센티 사람"

    def test_speak_overrides_previous(self) -> None:
        tts = MockTts()
        tts.speak("첫 번째", RiskLevel.HIGH)
        tts.speak("두 번째", RiskLevel.MID)
        assert tts.last_spoken == "두 번째"

    def test_stop_no_error(self) -> None:
        tts = MockTts()
        tts.stop()  # 예외 발생 없어야 함

    def test_speak_mid_level(self) -> None:
        tts = MockTts()
        tts.speak("왼쪽에 의자", RiskLevel.MID)
        assert tts.last_spoken == "왼쪽에 의자"


# ── EspeakTts 쿨다운 테스트 ───────────────────────────────────────────────────

class TestEspeakTts:
    def test_high_cooldown_blocks_second_call(self) -> None:
        tts = EspeakTts()
        with patch.object(tts, "_start_thread") as mock_start:
            tts.speak("첫 번째", RiskLevel.HIGH)
            tts.speak("두 번째", RiskLevel.HIGH)  # 쿨다운 중
            assert mock_start.call_count == 1

    def test_mid_cooldown_blocks_second_call(self) -> None:
        tts = EspeakTts()
        with patch.object(tts, "_start_thread") as mock_start:
            tts.speak("첫 번째 MID", RiskLevel.MID)
            tts.speak("두 번째 MID", RiskLevel.MID)  # 쿨다운 중
            assert mock_start.call_count == 1

    def test_high_kills_existing_thread(self) -> None:
        tts = EspeakTts()
        with patch.object(tts, "_kill_current") as mock_kill:
            with patch.object(tts, "_start_thread"):
                tts._last_mid_time = 0.0
                tts.speak("MID 텍스트", RiskLevel.MID)
                tts._last_high_time = 0.0
                tts.speak("HIGH 텍스트", RiskLevel.HIGH)
                mock_kill.assert_called()

    def test_mid_skips_if_thread_running(self) -> None:
        tts = EspeakTts()
        thread = MagicMock()
        thread.is_alive.return_value = True  # 발화 중
        tts._thread = thread
        tts._last_mid_time = 0.0  # 쿨다운 만료
        with patch.object(tts, "_start_thread") as mock_start:
            tts.speak("MID 텍스트", RiskLevel.MID)
            mock_start.assert_not_called()

    def test_stop_kills_thread(self) -> None:
        tts = EspeakTts()
        with patch.object(tts, "_kill_current") as mock_kill:
            tts.stop()
            mock_kill.assert_called_once()

    def test_stop_when_no_thread(self) -> None:
        tts = EspeakTts()
        tts.stop()  # 예외 발생 없어야 함

    def test_high_after_cooldown_speaks(self) -> None:
        tts = EspeakTts()
        with patch.object(tts, "_start_thread") as mock_start:
            tts.speak("첫 번째", RiskLevel.HIGH)
            tts._last_high_time = 0.0  # 쿨다운 만료
            tts.speak("두 번째", RiskLevel.HIGH)
            assert mock_start.call_count == 2


# ── 사전 렌더링 TTS 캐시 테스트 ─────────────────────────────────────────────────

class TestPrerenderedTts:
    def test_phrase_to_filename_slugifies_punctuation(self) -> None:
        assert phrase_to_filename("Danger! Obstacle ahead") == "danger__obstacle_ahead.wav"

    def _write_wav(self, path: Path, sample_rate: int, pcm_bytes: bytes) -> None:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)

    def test_load_prerendered_cache_reads_matching_wav(self, tmp_path, monkeypatch) -> None:
        pcm = b"\x01\x00" * 10
        self._write_wav(tmp_path / phrase_to_filename("hello"), 22050, pcm)
        monkeypatch.setattr(config, "TTS_PRERENDERED_DIR", str(tmp_path))
        monkeypatch.setattr(config, "TTS_PRERENDERED_PHRASES", ["hello"])

        cache = load_prerendered_cache(expected_sample_rate=22050)
        assert cache == {"hello": pcm}

    def test_load_prerendered_cache_skips_missing_file(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(config, "TTS_PRERENDERED_DIR", str(tmp_path))
        monkeypatch.setattr(config, "TTS_PRERENDERED_PHRASES", ["never rendered"])

        cache = load_prerendered_cache(expected_sample_rate=22050)
        assert cache == {}

    def test_load_prerendered_cache_skips_sample_rate_mismatch(self, tmp_path, monkeypatch) -> None:
        self._write_wav(tmp_path / phrase_to_filename("hello"), 16000, b"\x00\x00" * 10)
        monkeypatch.setattr(config, "TTS_PRERENDERED_DIR", str(tmp_path))
        monkeypatch.setattr(config, "TTS_PRERENDERED_PHRASES", ["hello"])

        cache = load_prerendered_cache(expected_sample_rate=22050)  # 모델 샘플레이트와 불일치
        assert cache == {}


# ── PiperTts 쿨다운 테스트 ────────────────────────────────────────────────────

def _make_piper_tts() -> PiperTts:
    """piper 라이브러리 없이 PiperTts를 생성하는 헬퍼.

    실제 사전 렌더링 WAV(models/tts/prerendered/)가 디스크에 있는지 여부와
    무관하게 결정적으로 동작하도록 load_prerendered_cache()를 빈 딕셔너리로
    고정한다. 사전 렌더링 캐시 자체를 검증하는 테스트는 tts._prerendered_cache를
    직접 채워 넣는다.
    """
    mock_voice = MagicMock()
    mock_voice.config.sample_rate = 22050
    MockPiperVoice = MagicMock()
    MockPiperVoice.load.return_value = mock_voice
    with patch.dict(sys.modules, {
        "piper": MagicMock(),
        "piper.voice": MagicMock(PiperVoice=MockPiperVoice),
    }), patch("audio.piper_tts.load_prerendered_cache", return_value={}):
        return PiperTts("fake.onnx")


class TestPiperTts:
    def test_high_cooldown_blocks_second_call(self) -> None:
        tts = _make_piper_tts()
        with patch.object(tts, "_start_thread") as mock_start:
            tts.speak("첫 번째", RiskLevel.HIGH)
            tts.speak("두 번째", RiskLevel.HIGH)  # 쿨다운 중
            assert mock_start.call_count == 1

    def test_mid_cooldown_blocks_second_call(self) -> None:
        tts = _make_piper_tts()
        with patch.object(tts, "_start_thread") as mock_start:
            tts.speak("첫 번째 MID", RiskLevel.MID)
            tts.speak("두 번째 MID", RiskLevel.MID)  # 쿨다운 중
            assert mock_start.call_count == 1

    def test_high_kills_existing_thread(self) -> None:
        tts = _make_piper_tts()
        with patch.object(tts, "_kill_current") as mock_kill:
            with patch.object(tts, "_start_thread"):
                tts._last_mid_time = 0.0
                tts.speak("MID 텍스트", RiskLevel.MID)
                tts._last_high_time = 0.0
                tts.speak("HIGH 텍스트", RiskLevel.HIGH)
                mock_kill.assert_called()

    def test_mid_skips_if_thread_running(self) -> None:
        tts = _make_piper_tts()
        thread = MagicMock()
        thread.is_alive.return_value = True
        tts._thread = thread
        tts._last_mid_time = 0.0
        with patch.object(tts, "_start_thread") as mock_start:
            tts.speak("MID 텍스트", RiskLevel.MID)
            mock_start.assert_not_called()

    def test_stop_kills_thread(self) -> None:
        tts = _make_piper_tts()
        with patch.object(tts, "_kill_current") as mock_kill:
            tts.stop()
            mock_kill.assert_called_once()

    def test_stop_when_no_thread(self) -> None:
        tts = _make_piper_tts()
        tts.stop()  # 예외 발생 없어야 함

    def test_high_after_cooldown_speaks(self) -> None:
        tts = _make_piper_tts()
        with patch.object(tts, "_start_thread") as mock_start:
            tts.speak("첫 번째", RiskLevel.HIGH)
            tts._last_high_time = 0.0  # 쿨다운 만료
            tts.speak("두 번째", RiskLevel.HIGH)
            assert mock_start.call_count == 2

    def test_speak_worker_caches_pcm_for_repeated_text(self) -> None:
        """동일 문구 재발화 시 신경망 합성을 재수행하지 않고 캐시된 PCM을 재사용한다."""
        tts = _make_piper_tts()
        stop_flag = threading.Event()
        tts._voice.synthesize_stream_raw.return_value = iter([b"\x00\x00" * 100])

        with patch.object(tts, "_stream") as mock_stream:
            tts._speak_worker("Danger! Obstacle ahead", stop_flag)
            tts._speak_worker("Danger! Obstacle ahead", stop_flag)

        assert tts._voice.synthesize_stream_raw.call_count == 1  # 두 번째는 캐시 재사용
        assert mock_stream.play.call_count == 2  # 재생 자체는 매번 수행

    def test_speak_worker_prefers_prerendered_cache_over_synthesis(self) -> None:
        """사전 렌더링 WAV가 있는 문구는 신경망 추론 자체를 건너뛴다."""
        tts = _make_piper_tts()
        tts._prerendered_cache = {"Danger! Obstacle ahead": b"\x11\x22" * 50}
        stop_flag = threading.Event()

        with patch.object(tts, "_stream") as mock_stream:
            tts._speak_worker("Danger! Obstacle ahead", stop_flag)

        tts._voice.synthesize_stream_raw.assert_not_called()
        mock_stream.play.assert_called_once()

    def test_speak_worker_aborts_on_stop_flag_during_synthesis(self) -> None:
        tts = _make_piper_tts()
        stop_flag = threading.Event()

        def slow_synthesize(_text):
            stop_flag.set()  # 합성 도중 선점 신호
            yield b"\x00\x00" * 100

        tts._voice.synthesize_stream_raw = slow_synthesize
        with patch.object(tts, "_stream") as mock_stream:
            tts._speak_worker("테스트", stop_flag)
        mock_stream.play.assert_not_called()  # 합성 중단 → 재생 호출 없어야 함

    def test_kill_current_clears_stream(self) -> None:
        """선점 시 상주 스트림 버퍼를 즉시 비워 재생 중인 오디오를 끊는다."""
        tts = _make_piper_tts()
        thread = MagicMock()
        thread.is_alive.return_value = True
        tts._thread = thread
        with patch.object(tts, "_stream") as mock_stream:
            tts._kill_current()
        mock_stream.clear.assert_called_once()

    def test_is_speaking_true_while_stream_playing(self) -> None:
        """합성 스레드가 끝났어도 상주 스트림이 재생 중이면 발화 중으로 간주한다."""
        tts = _make_piper_tts()
        tts._thread = None
        with patch.object(tts._stream, "is_playing", return_value=True):
            assert tts.is_speaking() is True


# ── _build_tts_text() 테스트 ──────────────────────────────────────────────────

class TestBuildTtsText:
    def test_none_risk_returns_none(self) -> None:
        result = FusionResult(RiskLevel.NONE, 200.0, tof_only_mode=False)
        assert _build_tts_text(result) is None

    def test_tof_only_high(self) -> None:
        result = FusionResult(RiskLevel.HIGH, 80.0, tof_only_mode=True)
        assert _build_tts_text(result) == "Danger! Obstacle ahead"

    def test_tof_only_mid(self) -> None:
        result = FusionResult(RiskLevel.MID, 130.0, tof_only_mode=True)
        assert _build_tts_text(result) == "Caution, obstacle"

    def test_high_detection_label(self) -> None:
        result = FusionResult(
            RiskLevel.HIGH, 80.0, tof_only_mode=False,
            top_label="person", direction="정면",
        )
        assert _build_tts_text(result) == "Danger! person, 80 centimeters, ahead"

    def test_mid_detection_label(self) -> None:
        result = FusionResult(
            RiskLevel.MID, 130.0, tof_only_mode=False,
            top_label="chair", direction="왼쪽",
        )
        assert _build_tts_text(result) == "chair on the left"

    def test_high_right_direction(self) -> None:
        result = FusionResult(
            RiskLevel.HIGH, 95.0, tof_only_mode=False,
            top_label="car", direction="오른쪽",
        )
        assert _build_tts_text(result) == "Danger! car, 95 centimeters, on the right"

    def test_unknown_label_in_text(self) -> None:
        result = FusionResult(
            RiskLevel.HIGH, 80.0, tof_only_mode=False,
            top_label="unknown_item", direction="정면",
        )
        text = _build_tts_text(result)
        assert text is not None
        assert "unknown_item" in text

    def test_no_label_uses_default(self) -> None:
        result = FusionResult(
            RiskLevel.HIGH, 80.0, tof_only_mode=False,
            top_label=None, direction="정면",
        )
        text = _build_tts_text(result)
        assert text is not None
        assert "Danger" in text
        assert "ahead" in text

    def test_no_direction_uses_default(self) -> None:
        result = FusionResult(
            RiskLevel.MID, 130.0, tof_only_mode=False,
            top_label="bench", direction=None,
        )
        text = _build_tts_text(result)
        assert text is not None
        assert "ahead" in text

    def test_distance_rounded(self) -> None:
        result = FusionResult(
            RiskLevel.HIGH, 82.7, tof_only_mode=False,
            top_label="person", direction="정면",
        )
        text = _build_tts_text(result)
        assert "83 centimeters" in text
