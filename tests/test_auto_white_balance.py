"""AutoWhiteBalance 제어 법칙 단위 테스트.

하드웨어 없이 합성 프레임으로 검증한다. 시간 의존 로직은 now를 인자로 주입하므로
sleep 없이 갱신 주기를 재현한다 (CLAUDE.md §4).
"""
import numpy as np

import config
from vision.auto_white_balance import AutoWhiteBalance

# 갱신 주기를 확실히 넘기는 시간 간격
STEP_SEC = config.CSI_AWB_UPDATE_INTERVAL_SEC + 0.01


def _tinted(b: int, g: int, r: int) -> np.ndarray:
    """채널별로 고정값을 가진 BGR 프레임을 만든다.

    Args:
        b: 블루 채널 값 (0~255).
        g: 그린 채널 값.
        r: 레드 채널 값.

    Returns:
        shape (H, W, 3) uint8 프레임.
    """
    frame = np.empty((config.FRAME_HEIGHT, config.FRAME_WIDTH, 3), dtype=np.uint8)
    frame[..., 0] = b
    frame[..., 1] = g
    frame[..., 2] = r
    return frame


def _converge(awb: AutoWhiteBalance, frame: np.ndarray, iterations: int = 60) -> np.ndarray:
    """같은 프레임을 반복 투입해 EMA가 수렴한 뒤의 출력 프레임을 반환한다.

    Args:
        awb: 대상 인스턴스.
        frame: 매 주기 투입할 프레임.
        iterations: 반복 횟수.

    Returns:
        마지막 보정 프레임.
    """
    out = frame
    for i in range(iterations):
        out = awb.update(i * STEP_SEC, frame)
    return out


def _channel_means(frame: np.ndarray) -> tuple[float, float, float]:
    """프레임의 (B, G, R) 채널 평균."""
    return tuple(float(frame[..., c].mean()) for c in range(3))


def test_green_cast_is_corrected_toward_neutral():
    """2026-08-04 야외 실측 채널비(R/G=0.56, B/G=0.74)를 중성으로 되돌린다."""
    # 보도블록 패치 실측값에 해당하는 프레임 (G가 지배적)
    frame = _tinted(b=91, g=124, r=71)
    out = _converge(AutoWhiteBalance(), frame)

    b, g, r = _channel_means(out)
    assert abs(r / g - 1.0) < 0.05, f"R/G가 중성으로 오지 않았습니다: {r / g:.3f}"
    assert abs(b / g - 1.0) < 0.05, f"B/G가 중성으로 오지 않았습니다: {b / g:.3f}"


def test_neutral_frame_is_left_alone():
    """이미 무채색인 장면은 과보정하지 않는다."""
    frame = _tinted(b=120, g=120, r=120)
    awb = AutoWhiteBalance()
    _converge(awb, frame)

    for gain in awb.gains:
        assert abs(gain - 1.0) < 0.02, f"중성 장면에서 게인이 흔들렸습니다: {awb.gains}"


def test_overall_brightness_is_preserved():
    """채널 평균을 전체 평균에 맞추므로 프레임 밝기는 보존된다.

    특정 채널을 1.0으로 고정하면 전체가 밝아져 AE 루프와 간섭한다.
    """
    frame = _tinted(b=91, g=124, r=71)
    before = float(frame.mean())
    after = float(_converge(AutoWhiteBalance(), frame).mean())

    assert abs(after - before) / before < 0.05, (
        f"보정 전후 밝기가 크게 달라졌습니다: {before:.1f} → {after:.1f}"
    )


def test_gain_update_is_throttled_by_interval():
    """갱신 주기 안에서는 게인을 다시 계산하지 않는다."""
    awb = AutoWhiteBalance()
    frame = _tinted(b=91, g=124, r=71)

    awb.update(0.0, frame)
    first = awb.gains
    awb.update(config.CSI_AWB_UPDATE_INTERVAL_SEC * 0.5, frame)

    assert awb.gains == first, "주기 이전에 게인이 갱신됐습니다"


def test_gain_moves_gradually_not_in_one_step():
    """EMA로 섞으므로 첫 측광에 목표 게인으로 점프하지 않는다.

    장면이 바뀔 때마다 색이 출렁이는 것을 막는 장치다.
    """
    awb = AutoWhiteBalance()
    frame = _tinted(b=91, g=124, r=71)

    awb.update(0.0, frame)
    after_first = awb.gains
    _converge(awb, frame)
    settled = awb.gains

    # 레드 게인(가장 크게 움직이는 채널)이 한 번에 최종값에 도달하면 안 된다.
    assert after_first[2] < settled[2] * 0.9, (
        f"첫 갱신에 게인이 목표까지 점프했습니다: {after_first[2]:.3f} → {settled[2]:.3f}"
    )


def test_gains_are_clamped_on_single_color_scene():
    """한 색이 지배하는 장면에서 게인이 한계를 넘지 않는다.

    그레이월드는 "장면 평균은 무채색"을 가정하므로 잔디밭 같은 장면에서 과보정한다.
    """
    frame = _tinted(b=20, g=200, r=20)
    awb = AutoWhiteBalance()
    _converge(awb, frame)

    for gain in awb.gains:
        assert config.CSI_AWB_GAIN_MIN <= gain <= config.CSI_AWB_GAIN_MAX, (
            f"게인이 클램프 범위를 벗어났습니다: {awb.gains}"
        )


def test_skips_update_when_no_valid_pixels():
    """전부 클리핑되거나 전부 암흑이면 게인을 갱신하지 않는다.

    클리핑 픽셀은 세 채널이 모두 255라 게인 추정을 망친다.
    """
    awb = AutoWhiteBalance()

    awb.update(0.0, _tinted(b=255, g=255, r=255))
    assert awb.gains == (1.0, 1.0, 1.0), f"화이트아웃 프레임이 게인을 바꿨습니다: {awb.gains}"

    awb.update(STEP_SEC, _tinted(b=2, g=2, r=2))
    assert awb.gains == (1.0, 1.0, 1.0), f"암흑 프레임이 게인을 바꿨습니다: {awb.gains}"


def test_returns_original_frame_before_any_measurement():
    """측광이 한 번도 성공하지 않았으면 입력 프레임을 그대로 통과시킨다."""
    awb = AutoWhiteBalance()
    frame = _tinted(b=255, g=255, r=255)

    assert awb.update(0.0, frame) is frame


def test_output_dtype_and_shape_are_unchanged():
    """보정 결과가 추론·클립 저장이 기대하는 형식을 유지한다."""
    frame = _tinted(b=91, g=124, r=71)
    out = _converge(AutoWhiteBalance(), frame)

    assert out.dtype == np.uint8
    assert out.shape == frame.shape


def test_disabled_by_config_is_respected_by_camera_hal(monkeypatch):
    """CSI_AWB_ENABLED=False면 HAL이 AWB를 만들지 않는다 (원인 격리 스위치)."""
    from vision.csi_camera_hal import CSICameraHAL

    monkeypatch.setattr(config, "CSI_AWB_ENABLED", False)
    assert CSICameraHAL()._awb is None

    monkeypatch.setattr(config, "CSI_AWB_ENABLED", True)
    assert CSICameraHAL()._awb is not None
