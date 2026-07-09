"""Orange Pi 5 액티브 쿨러 PWM 제어 스크립트.

CPU 온도를 5초 주기로 읽어 PWM 듀티 사이클을 자동 조정한다.
별도 프로세스(systemd 서비스 또는 수동 실행)로 main.py와 독립적으로 동작한다.

사용법:
    sudo python scripts/pwm_fan_control.py

Orange Pi 5 PWM 경로:
    /sys/class/pwm/pwmchip0/pwm0/
"""
import logging
import signal
import sys
import time
from pathlib import Path
from types import FrameType
from typing import List, Optional, Tuple

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# PWM sysfs 경로 (Orange Pi 5 pwmchip0, channel 0)
_PWM_CHIP_PATH: Path = Path("/sys/class/pwm/pwmchip0")
_PWM_CHANNEL_PATH: Path = _PWM_CHIP_PATH / "pwm0"
_CPU_TEMP_PATH: Path = Path(config.CPU_TEMP_SYSFS_PATH)

_PERIOD_NS: int = 25_000  # 40kHz PWM (25µs 주기)
_CHECK_INTERVAL_SEC: float = 5.0

# 온도-듀티 매핑 테이블: (온도°C, 듀티 비율 0.0~1.0)
_TEMP_DUTY_TABLE: List[Tuple[float, float]] = [
    (50.0, 0.20),
    (70.0, 0.80),
    (80.0, 1.00),
]


def _read_cpu_temp() -> float:
    """CPU 온도(°C)를 반환한다. 읽기 실패 시 0.0."""
    try:
        return int(_CPU_TEMP_PATH.read_text().strip()) / 1000.0
    except OSError:
        return 0.0


def _temp_to_duty(temp: float) -> float:
    """온도를 듀티 사이클 비율(0.0~1.0)로 선형 보간한다."""
    if temp < _TEMP_DUTY_TABLE[0][0]:
        return _TEMP_DUTY_TABLE[0][1]
    for i in range(len(_TEMP_DUTY_TABLE) - 1):
        t0, d0 = _TEMP_DUTY_TABLE[i]
        t1, d1 = _TEMP_DUTY_TABLE[i + 1]
        if t0 <= temp <= t1:
            ratio = (temp - t0) / (t1 - t0)
            return d0 + ratio * (d1 - d0)
    return _TEMP_DUTY_TABLE[-1][1]


def _pwm_init() -> bool:
    """PWM 채널을 초기화한다. 성공 시 True, 실패 시 False."""
    try:
        if not _PWM_CHANNEL_PATH.exists():
            (_PWM_CHIP_PATH / "export").write_text("0")
            time.sleep(0.1)
        # duty_cycle을 0으로 먼저 리셋해야 period 쓰기 시 EINVAL 방지 (duty_cycle <= period 규칙)
        (_PWM_CHANNEL_PATH / "duty_cycle").write_text("0")
        (_PWM_CHANNEL_PATH / "period").write_text(str(_PERIOD_NS))
        (_PWM_CHANNEL_PATH / "enable").write_text("1")
        logger.info("PWM 초기화 완료: 주기 %dns", _PERIOD_NS)
        return True
    except OSError as exc:
        logger.error("PWM 초기화 실패 (root 권한 필요?): %s", exc)
        return False


def _pwm_set_duty(duty: float) -> None:
    """듀티 사이클을 설정한다. duty: 0.0~1.0."""
    duty_ns = int(_PERIOD_NS * max(0.0, min(1.0, duty)))
    try:
        (_PWM_CHANNEL_PATH / "duty_cycle").write_text(str(duty_ns))
    except OSError as exc:
        logger.error("PWM 듀티 설정 실패: %s", exc)


def _pwm_disable() -> None:
    """PWM을 비활성화하고 채널을 해제한다."""
    try:
        (_PWM_CHANNEL_PATH / "enable").write_text("0")
        (_PWM_CHIP_PATH / "unexport").write_text("0")
        logger.info("PWM 비활성화 완료")
    except OSError:
        pass


def main() -> None:
    """PWM 팬 제어 메인 루프."""
    if not _CPU_TEMP_PATH.exists():
        logger.error("CPU 온도 경로 없음: %s (Orange Pi 5에서만 동작)", _CPU_TEMP_PATH)
        sys.exit(1)

    if not _pwm_init():
        sys.exit(1)

    def _on_signal(signum: int, frame: Optional[FrameType]) -> None:
        logger.info("종료 신호 수신, PWM 비활성화")
        _pwm_disable()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    logger.info("PWM 팬 제어 시작 (확인 주기: %.0fs)", _CHECK_INTERVAL_SEC)
    while True:
        temp = _read_cpu_temp()
        duty = _temp_to_duty(temp)
        _pwm_set_duty(duty)
        logger.info("CPU %.1f°C → 듀티 %.0f%%", temp, duty * 100)
        time.sleep(_CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
