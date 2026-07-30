"""카메라 색 채널 균형 진단 (v2.0 Phase 5-1b — AWB 도입 판단용).

2026-07-29 야외 테스트에서 저장된 이벤트 클립에 **전면 녹색 캐스팅**이 관찰됐다.
다만 그것이 화이트밸런스 문제인지는 아직 확정되지 않았다. 전면 균일 녹색은 WB보다
**디모자이크/픽셀포맷 불일치**(`csi_camera_hal._setup_isp_pipeline()`이 SBGGR10_1X10을
UYVY로 변환한다)의 징후일 수 있고, 그레이월드 게인으로 덮으면 진짜 원인을 가린다.

이 스크립트는 그 분기를 판단할 숫자를 뽑는다. Orange Pi 5에서 직접 실행할 것.

판정 기준:
    G/R·G/B 비율 1.5 ~ 2.5배
        → 화이트밸런스 문제. 소프트웨어 그레이월드 게인으로 보정 가능.
    G/R·G/B 비율 4배 이상, 또는 채널 상관이 무너짐(같은 장면인데 채널별 구조가 다름)
        → 포맷/디모자이크 문제. media-ctl 포맷 체인을 먼저 점검해야 하고 AWB는 보류.
    비율이 1에 가까움
        → 색 균형은 정상. 클립의 녹색은 조명/장면 탓이므로 AWB 불필요.

⚠️ `/dev/video11`을 여므로 운영 서비스와 충돌한다. 먼저 서비스를 멈출 것::

    ssh -t raseyes "sudo systemctl stop raseyes.service"
    python3 scripts/wb_probe.py --frames 30
    ssh -t raseyes "sudo systemctl start raseyes.service"

사용법:
    python3 scripts/wb_probe.py                      # 30프레임 측정
    python3 scripts/wb_probe.py --frames 60 --save /tmp/wb  # 프레임도 JPEG로 저장
"""
import argparse
import statistics
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import config  # noqa: E402
from vision.csi_camera_hal import CSICameraHAL  # noqa: E402


def _channel_means(frame: np.ndarray) -> tuple:
    """BGR 프레임의 채널별 평균을 반환한다.

    Args:
        frame: BGR 프레임.

    Returns:
        (B 평균, G 평균, R 평균).
    """
    b, g, r, _ = cv2.mean(frame)
    return b, g, r


def _report(samples: List[tuple]) -> None:
    """수집한 채널 평균들을 요약해 인쇄하고 판정을 제시한다.

    Args:
        samples: (B, G, R) 튜플 목록.
    """
    b_vals = [s[0] for s in samples]
    g_vals = [s[1] for s in samples]
    r_vals = [s[2] for s in samples]
    b_mean = statistics.fmean(b_vals)
    g_mean = statistics.fmean(g_vals)
    r_mean = statistics.fmean(r_vals)

    print("\n── 채널 평균 ({}프레임) ─────────────────────────".format(len(samples)))
    print("  B : {:6.1f}  (σ {:.1f})".format(b_mean, statistics.pstdev(b_vals)))
    print("  G : {:6.1f}  (σ {:.1f})".format(g_mean, statistics.pstdev(g_vals)))
    print("  R : {:6.1f}  (σ {:.1f})".format(r_mean, statistics.pstdev(r_vals)))

    gr = g_mean / r_mean if r_mean > 0 else float("inf")
    gb = g_mean / b_mean if b_mean > 0 else float("inf")
    print("\n── 비율 ─────────────────────────")
    print("  G/R : {:.2f}".format(gr))
    print("  G/B : {:.2f}".format(gb))

    worst = max(gr, gb)
    print("\n── 판정 ─────────────────────────")
    if worst >= 4.0:
        print("  ⚠ 포맷/디모자이크 문제 의심 (비율 {:.2f}배)".format(worst))
        print("    그레이월드로 덮지 말 것. media-ctl 포맷 체인부터 점검한다:")
        print("    - SBGGR10_1X10 → UYVY 변환 경로 (csi_camera_hal._setup_isp_pipeline)")
        print("    - 베이어 패턴 순서(BGGR/RGGB) 불일치 여부")
    elif worst >= 1.5:
        print("  → 화이트밸런스 문제. 그레이월드 게인으로 보정 가능하다.")
        print("    보정 게인: B x{:.2f}, R x{:.2f} (G 기준)".format(gb, gr))
    else:
        print("  → 색 균형 정상 (비율 {:.2f}배). AWB 불필요.".format(worst))
        print("    클립의 녹색은 장면/조명 탓일 가능성이 높다.")


def main() -> int:
    """CLI 진입점.

    Returns:
        종료 코드 (0=정상, 1=카메라 열기 실패).
    """
    parser = argparse.ArgumentParser(description="카메라 채널 균형 진단 (AWB 판단용)")
    parser.add_argument("--frames", type=int, default=30, help="측정할 프레임 수 (기본 30)")
    parser.add_argument("--save", default=None, help="프레임을 JPEG로 저장할 디렉터리 (선택)")
    args = parser.parse_args()

    camera = CSICameraHAL()
    try:
        camera.start()
    except RuntimeError as exc:
        print("카메라를 열 수 없습니다: {}".format(exc))
        print("운영 서비스가 장치를 잡고 있지 않은지 확인하세요 "
              "(sudo systemctl stop raseyes.service).")
        return 1

    save_dir = None
    if args.save:
        save_dir = Path(args.save)
        save_dir.mkdir(parents=True, exist_ok=True)

    samples: List[tuple] = []
    try:
        for i in range(args.frames):
            frame = camera.read_frame()
            samples.append(_channel_means(frame))
            if save_dir is not None:
                cv2.imwrite(
                    str(save_dir / "wb_{:03d}.jpg".format(i)),
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, config.CLIP_JPEG_QUALITY],
                )
    except RuntimeError as exc:
        print("프레임 캡처 실패: {}".format(exc))
        return 1
    finally:
        camera.stop()

    if not samples:
        print("수집된 프레임이 없습니다.")
        return 1

    _report(samples)
    if save_dir is not None:
        print("\n프레임 {}장 저장: {}".format(len(samples), save_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
