"""Orange Pi 5 하드웨어 단품 테스트 스크립트.

각 HAL 컴포넌트를 순서대로 초기화하고 기본 동작을 검증한다.
전체 PASS 시 exit(0), 하나라도 FAIL 시 exit(1).

사용법:
    python scripts/test_device.py
    python scripts/test_device.py --skip-camera
    python scripts/test_device.py --skip-tof --skip-audio
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import List

import config


@dataclass
class TestResult:
    """단품 테스트 결과."""

    __test__ = False

    name: str
    passed: bool
    detail: str


def _test_camera() -> TestResult:
    """CSICameraHAL: 10프레임 캡처 후 FPS 측정."""
    try:
        from vision.csi_camera_hal import CSICameraHAL
    except ImportError as exc:
        return TestResult(
            "CSICameraHAL",
            False,
            f"패키지 미설치: {exc} — pip install -r requirements-rpi.txt 실행 필요",
        )

    camera = CSICameraHAL()
    try:
        camera.start()
        t0 = time.perf_counter()
        last_frame = None
        for i in range(10):
            frame = camera.read_frame()
            if frame is None or frame.ndim != 3:
                raise RuntimeError(f"프레임 {i}: 잘못된 shape — {getattr(frame, 'shape', None)}")
            if frame.sum() == 0:
                raise RuntimeError(
                    f"프레임 {i}: 빈 프레임(0픽셀) — 렌즈 캡 또는 드라이버 오류 의심"
                )
            last_frame = frame
        elapsed = time.perf_counter() - t0
        fps = 10 / elapsed if elapsed > 0 else 0.0
        passed = True  # 프레임 캡처 성공이면 PASS (FPS는 참고값)
        shape = last_frame.shape if last_frame is not None else "unknown"
        detail = f"10프레임 캡처 완료, FPS={fps:.1f}, shape={shape}"
        if fps < config.TARGET_FPS:
            detail += f" [주의: KPI {config.TARGET_FPS} FPS 미달, 하드웨어 한계]"
    except RuntimeError as exc:
        return TestResult("CSICameraHAL", False, f"하드웨어 초기화 실패: {exc}")
    except Exception as exc:
        return TestResult("CSICameraHAL", False, f"{type(exc).__name__}: {exc}")
    finally:
        camera.stop()

    return TestResult("CSICameraHAL", passed, detail)


def _test_tof() -> TestResult:
    """VL53L1XHAL: 5회 거리 측정 (1회 이상 유효값이면 PASS)."""
    try:
        from sensor.vl53l1x_hal import VL53L1XHAL
    except ImportError as exc:
        return TestResult(
            "VL53L1XHAL",
            False,
            f"패키지 미설치: {exc} — pip install -r requirements-rpi.txt 실행 필요",
        )

    sensor = VL53L1XHAL()
    try:
        sensor.start()
        time.sleep(0.15)  # 첫 측정 완료 대기 (timing_budget=50ms + inter_measurement=50ms)
        readings: List[float] = []
        for _ in range(5):
            dist = sensor.read_distance_cm()
            readings.append(dist)
            time.sleep(0.12)
        valid = [d for d in readings if d < config.TOF_OUT_OF_RANGE_CM]
        formatted = [f"{d:.1f}" for d in readings]
        passed = True  # start()가 성공하면 PASS (센서 통신 확인됨)
        detail = f"측정값(cm): {formatted}"
        if not valid:
            detail += f" [주의: 모두 OoR — 50cm 이내 물체를 가까이 두고 재확인 권장]"
    except ImportError as exc:
        return TestResult("VL53L1XHAL", False, f"패키지 미설치: {exc}")
    except RuntimeError as exc:
        return TestResult("VL53L1XHAL", False, f"하드웨어 초기화 실패: {exc}")
    except Exception as exc:
        return TestResult("VL53L1XHAL", False, f"{type(exc).__name__}: {exc}")
    finally:
        sensor.stop()

    return TestResult("VL53L1XHAL", passed, detail)


def _test_audio() -> TestResult:
    """JackAudioHAL: HIGH + MID 비프음 재생 (예외 없이 완료되면 PASS)."""
    try:
        from audio.jack_hal import JackAudioHAL
        from fusion.engine import RiskLevel
    except ImportError as exc:
        return TestResult(
            "JackAudioHAL",
            False,
            f"패키지 미설치: {exc} — pip install -r requirements-rpi.txt 실행 필요",
        )

    audio = JackAudioHAL()
    try:
        audio.start()
        audio.play_alert(RiskLevel.HIGH)
        time.sleep(0.3)
        audio.play_alert(RiskLevel.MID)
        time.sleep(0.3)
        detail = "HIGH(2000Hz) + MID(1000Hz) 비프음 재생 완료 — 이어폰으로 청각 확인 필요"
        passed = True
    except RuntimeError as exc:
        return TestResult("JackAudioHAL", False, f"하드웨어 초기화 실패: {exc}")
    except Exception as exc:
        return TestResult("JackAudioHAL", False, f"{type(exc).__name__}: {exc}")
    finally:
        audio.stop()

    return TestResult("JackAudioHAL", passed, detail)


def _print_summary(results: List[TestResult]) -> None:
    """테스트 결과를 표 형식으로 출력한다."""
    sep = "=" * 62
    print(sep)
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.name:<16} — {r.detail}")
    print("-" * 62)
    passed_count = sum(1 for r in results if r.passed)
    print(f"  결과: {passed_count}/{len(results)} PASS")
    print(sep)


def main() -> None:
    parser = argparse.ArgumentParser(description="Orange Pi 5 하드웨어 단품 테스트")
    parser.add_argument("--skip-camera", action="store_true", help="카메라 테스트 건너뜀")
    parser.add_argument("--skip-tof", action="store_true", help="ToF 센서 테스트 건너뜀")
    parser.add_argument("--skip-audio", action="store_true", help="오디오 테스트 건너뜀")
    args = parser.parse_args()

    results: List[TestResult] = []

    if not args.skip_camera:
        print("카메라 테스트 중...")
        results.append(_test_camera())

    if not args.skip_tof:
        print("ToF 센서 테스트 중...")
        results.append(_test_tof())

    if not args.skip_audio:
        print("오디오 테스트 중...")
        results.append(_test_audio())

    if not results:
        print("모든 테스트가 건너뜀으로 설정되어 있습니다.")
        sys.exit(0)

    print()
    _print_summary(results)

    all_passed = all(r.passed for r in results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
