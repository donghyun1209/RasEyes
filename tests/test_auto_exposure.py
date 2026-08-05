"""AutoExposure 제어 법칙 단위 테스트.

하드웨어 없이 합성 프레임으로 검증한다. 시간 의존 로직은 now를 인자로 주입하므로
sleep 없이 settle/스로틀 구간을 재현한다 (CLAUDE.md §4).
"""
from typing import Optional, Tuple

import numpy as np
import pytest

import config
from vision.auto_exposure import AutoExposure

# 스로틀·settle을 모두 넘기는 시간 간격
STEP_SEC = max(config.CSI_AE_UPDATE_INTERVAL_SEC, config.CSI_AE_SETTLE_SEC) + 0.01


def _uniform(luma: int) -> np.ndarray:
    """전체가 같은 밝기인 BGR 프레임을 만든다.

    Args:
        luma: 0~255 밝기. BGR 3채널이 모두 같으면 그레이 변환 결과도 같은 값이다.

    Returns:
        shape (H, W, 3) uint8 프레임.
    """
    return np.full(
        (config.FRAME_HEIGHT, config.FRAME_WIDTH, 3), luma, dtype=np.uint8
    )


def _total(exposure: int, gain: int) -> float:
    """exposure와 gain을 합친 상대 노출량."""
    return exposure * (gain / config.CSI_AE_GAIN_MIN)


def _sim_frame(exposure: int, gain: int, scene: float) -> np.ndarray:
    """센서 모델: 노출량 x 장면 밝기 = 휘도 (255에서 포화).

    Args:
        exposure: 현재 센서 노출값.
        gain: 현재 센서 아날로그 게인.
        scene: 장면 밝기 계수. 클수록 밝은 환경.

    Returns:
        해당 노출 설정에서 센서가 낼 프레임.
    """
    luma = min(255.0, _total(exposure, gain) * scene)
    return _uniform(int(round(luma)))


class TestMetering:
    """측광 신호 검증."""

    def test_전백_프레임은_노출을_줄인다(self) -> None:
        ae = AutoExposure(exposure=1500, gain=config.CSI_AE_GAIN_MIN)
        result = ae.update(0.0, _uniform(255))

        assert result is not None
        assert _total(*result) < _total(1500, config.CSI_AE_GAIN_MIN)

    def test_전흑_프레임은_노출을_올린다(self) -> None:
        ae = AutoExposure(exposure=1500, gain=config.CSI_AE_GAIN_MIN)
        result = ae.update(0.0, _uniform(0))

        assert result is not None
        assert _total(*result) > _total(1500, config.CSI_AE_GAIN_MIN)

    def test_부분_화이트아웃은_평균이_목표여도_감광한다(self) -> None:
        """평균 휘도만 보면 속는 케이스.

        절반 가까이 날아간 프레임도 평균은 목표치 근처에 남는다. 클리핑 비율을
        함께 보지 않으면 "정상 노출"로 오판해 화이트아웃을 방치하게 된다.
        """
        frame = _uniform(0)
        clipped_rows = int(config.FRAME_HEIGHT * config.CSI_AE_TARGET_LUMA / 255.0)
        frame[:clipped_rows, :, :] = 255

        ae = AutoExposure(exposure=1500, gain=config.CSI_AE_GAIN_MIN)
        result = ae.update(0.0, frame)

        # 평균만 보면 데드밴드 안이라 "손댈 것 없음"으로 판정될 프레임이다
        assert abs(ae.mean_luma - config.CSI_AE_TARGET_LUMA) <= config.CSI_AE_TOLERANCE
        assert result is not None
        assert _total(*result) < _total(1500, config.CSI_AE_GAIN_MIN)

    def test_목표_휘도에서는_아무것도_하지_않는다(self) -> None:
        ae = AutoExposure(exposure=1500, gain=config.CSI_AE_GAIN_MIN)
        result = ae.update(0.0, _uniform(int(config.CSI_AE_TARGET_LUMA)))

        assert result is None
        assert ae.settled is True


class TestThrottling:
    """settle 창과 갱신 주기 검증 — v4l2 호출 빈도와 헌팅을 좌우한다."""

    def test_갱신_주기_안에서는_측광하지_않는다(self) -> None:
        ae = AutoExposure(exposure=1500, gain=config.CSI_AE_GAIN_MIN)
        ae.update(0.0, _uniform(int(config.CSI_AE_TARGET_LUMA)))

        assert ae.update(config.CSI_AE_UPDATE_INTERVAL_SEC * 0.5, _uniform(255)) is None

    def test_적용_직후_settle_구간에는_보정하지_않는다(self) -> None:
        """센서 반영 지연 중에 또 스텝을 밟으면 오버슛 → 헌팅이 된다."""
        ae = AutoExposure(exposure=1500, gain=config.CSI_AE_GAIN_MIN)
        applied = ae.update(0.0, _uniform(255))
        assert applied is not None

        # 갱신 주기는 지났지만 settle 창은 아직 안 지난 시각
        assert config.CSI_AE_SETTLE_SEC > config.CSI_AE_UPDATE_INTERVAL_SEC * 0.5
        probe = min(config.CSI_AE_SETTLE_SEC, config.CSI_AE_UPDATE_INTERVAL_SEC) - 0.01
        assert ae.update(probe, _uniform(255)) is None


class TestRails:
    """하드웨어 범위 클램프 검증."""

    def test_하한에_걸리면_더_줄이지_않는다(self) -> None:
        ae = AutoExposure(
            exposure=config.CSI_AE_EXPOSURE_MIN, gain=config.CSI_AE_GAIN_MIN
        )
        result = ae.update(0.0, _uniform(255))

        assert result is None
        # 더 조정할 수 없으므로 저전력 진입을 막지 않는다
        assert ae.settled is True

    def test_상한에_걸리면_더_올리지_않는다(self) -> None:
        ae = AutoExposure(
            exposure=config.CSI_AE_EXPOSURE_MAX, gain=config.CSI_AE_GAIN_MAX
        )
        result = ae.update(0.0, _uniform(0))

        assert result is None
        assert ae.settled is True

    def test_계산값은_항상_하드웨어_범위_안이다(self) -> None:
        ae = AutoExposure(exposure=1500, gain=config.CSI_AE_GAIN_MIN)
        now = 0.0
        for luma in (0, 255, 0, 255, 10, 240):
            now += STEP_SEC
            result = ae.update(now, _uniform(luma))
            if result is None:
                continue
            exposure, gain = result
            assert config.CSI_AE_EXPOSURE_MIN <= exposure <= config.CSI_AE_EXPOSURE_MAX
            assert config.CSI_AE_GAIN_MIN <= gain <= config.CSI_AE_GAIN_MAX


class TestClosedLoop:
    """센서 모델을 물려 실제 수렴·헌팅을 본다."""

    def _converge(
        self, scene: float, max_steps: int = 30
    ) -> Tuple[AutoExposure, int, int, int]:
        """수렴할 때까지 루프를 돌린다.

        Args:
            scene: 장면 밝기 계수.
            max_steps: 최대 반복 횟수.

        Returns:
            (AutoExposure, 최종 exposure, 최종 gain, 소요 스텝 수).
        """
        exposure = config.CSI_SENSOR_EXPOSURE
        gain = config.CSI_SENSOR_GAIN
        ae = AutoExposure(exposure=exposure, gain=gain)
        now = 0.0
        for step in range(1, max_steps + 1):
            now += STEP_SEC
            result: Optional[Tuple[int, int]] = ae.update(
                now, _sim_frame(exposure, gain, scene)
            )
            if result is None:
                return ae, exposure, gain, step
            exposure, gain = result
        pytest.fail(f"{max_steps} 스텝 안에 수렴하지 못했습니다 (scene={scene})")

    @pytest.mark.parametrize("scene", [0.5, 0.05, 0.005])
    def test_다양한_조도에서_수렴한다(self, scene: float) -> None:
        ae, exposure, gain, steps = self._converge(scene)

        assert abs(ae.mean_luma - config.CSI_AE_TARGET_LUMA) <= config.CSI_AE_TOLERANCE
        assert ae.settled is True
        # 0.3초 주기 기준 3초 안에는 잡혀야 그늘↔햇빛 전환을 따라간다
        assert steps <= 10

    def test_수렴_후에는_진동하지_않는다(self) -> None:
        """데드밴드에 든 뒤 계속 돌려도 v4l2 쓰기가 다시 발생하면 안 된다."""
        ae, exposure, gain, _ = self._converge(0.5)

        now = 100.0
        for _ in range(20):
            now += STEP_SEC
            assert ae.update(now, _sim_frame(exposure, gain, 0.5)) is None

    def test_화이트아웃에서_빠르게_탈출한다(self) -> None:
        """직사광 진입 시나리오 — 클리핑 경로가 기하급수로 걷어내야 한다."""
        ae, _, _, steps = self._converge(scene=5.0)

        assert steps <= 10
        assert abs(ae.mean_luma - config.CSI_AE_TARGET_LUMA) <= config.CSI_AE_TOLERANCE

    def test_밤처럼_어두우면_레일에서_멈춘다(self) -> None:
        """최대 노출로도 목표에 못 미치는 상황에서 무한 조정에 빠지지 않아야 한다."""
        ae, exposure, gain, _ = self._converge(scene=0.00001)

        assert (exposure, gain) == (config.CSI_AE_EXPOSURE_MAX, config.CSI_AE_GAIN_MAX)
        assert ae.mean_luma < config.CSI_AE_TARGET_LUMA
        # 레일에서는 저전력 진입을 막지 않는다 (영원히 수렴 못 하므로)
        assert ae.settled is True


class TestExposureReporting:
    """노출/게인 CSV 로깅 경로 (2026-08-04 추가).

    블러의 원인이 노출 시간인지 보행 중 움직임인지 사후에 가르려면 세션 로그에
    실제 노출값이 남아 있어야 한다.
    """

    def test_시드값을_그대로_보고한다(self) -> None:
        """측광 전에는 생성자에 넣은 시드가 현재값이다."""
        ae = AutoExposure(exposure=1234, gain=567)

        assert ae.exposure_gain == (1234, 567)

    def test_적용한_값을_보고한다(self) -> None:
        """센서에 쓴 값과 보고값이 어긋나면 로그로 튜닝할 수 없다."""
        ae = AutoExposure()
        applied: Optional[Tuple[int, int]] = None
        now = 0.0
        for _ in range(20):
            now += STEP_SEC
            result = ae.update(now, _uniform(20))
            if result is not None:
                applied = result

        assert applied is not None, "어두운 프레임인데 노출이 한 번도 갱신되지 않았습니다"
        assert ae.exposure_gain == applied

    def test_노출은_모션블러_상한을_넘지_않는다(self) -> None:
        """CSI_AE_EXPOSURE_MAX는 하드웨어 상한이 아니라 블러 상한이다."""
        ae = AutoExposure()
        now = 0.0
        for _ in range(40):
            now += STEP_SEC
            ae.update(now, _uniform(1))

        assert ae.exposure_gain[0] <= config.CSI_AE_EXPOSURE_MAX


class TestCameraHalReporting:
    """CSICameraHAL이 AE 상태를 상위 계층으로 올리는 경로."""

    def test_AE_비활성이면_노출을_보고하지_않는다(self, monkeypatch) -> None:
        """노출 제어가 없는 구성에서는 0이 아니라 None(결측)이어야 한다.

        0으로 기록하면 analyze_logs가 "노출 0"으로 집계해 날짜 간 비교가 망가진다.
        """
        from vision.csi_camera_hal import CSICameraHAL

        monkeypatch.setattr(config, "CSI_AE_ENABLED", False)
        assert CSICameraHAL().exposure_gain is None

    def test_AE_활성이면_현재_노출을_전달한다(self, monkeypatch) -> None:
        from vision.csi_camera_hal import CSICameraHAL

        monkeypatch.setattr(config, "CSI_AE_ENABLED", True)
        assert CSICameraHAL().exposure_gain == (
            config.CSI_SENSOR_EXPOSURE,
            config.CSI_SENSOR_GAIN,
        )
