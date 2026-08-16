"""내장 전원 버튼(rk805 pwrkey) evdev 이벤트 핸들러 (Orange Pi 5, STEP4 스캔 트리거)."""
import logging
import select
import threading
from typing import Callable, Optional

import config

logger = logging.getLogger(__name__)

_STOP_CHECK_TIMEOUT_SEC = 0.5  # select() 블로킹 대기 상한 (stop_event 확인 주기)


class PowerButtonHandler:
    """내장 전원 버튼을 배타적으로 grab해 스캔 트리거로 쓰는 핸들러.

    evdev 2.x 기반. lazy import이므로 PC에서도 임포트 오류 없이 사용 가능.
    grab() 중에는 systemd-logind가 이 버튼의 이벤트를 못 받으므로, 이 핸들러가
    살아있는 동안 물리 전원 버튼은 종료가 아니라 오직 on_press 콜백으로만 동작한다.

    Args:
        device_name: evdev 입력 장치 이름 (`/proc/bus/input/devices`의 `N:` 값).
    """

    def __init__(self, device_name: str = config.POWER_BUTTON_DEVICE_NAME) -> None:
        self._device_name = device_name
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, on_press: Callable[[], None]) -> None:
        """전원 버튼 폴링 daemon 스레드를 시작한다.

        Args:
            on_press: 버튼 누름 감지 시 호출할 콜백 (인자 없음).

        Raises:
            RuntimeError: evdev 패키지 미설치 시, 또는 입력 장치를 못 찾을 시.
        """
        try:
            import evdev  # lazy import (Orange Pi 5 전용)
        except ImportError as exc:
            raise RuntimeError(
                "evdev가 필요합니다: pip3 install --user evdev"
            ) from exc

        device = self._find_device(evdev)
        if device is None:
            raise RuntimeError(f"입력 장치 '{self._device_name}'를 찾을 수 없습니다")

        try:
            device.grab()
        except Exception as exc:
            device.close()
            raise RuntimeError(f"전원 버튼 grab 실패 (다른 프로세스가 점유 중?): {exc}") from exc

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            args=(on_press, device),
            daemon=True,
            name="power-button-handler",
        )
        self._thread.start()
        logger.info("PowerButtonHandler 시작 (device=%s)", self._device_name)

    def _find_device(self, evdev):
        for path in evdev.list_devices():
            device = evdev.InputDevice(path)
            if device.name == self._device_name:
                return device
        return None

    def _poll_loop(self, on_press: Callable[[], None], device) -> None:
        """전원 버튼 눌림을 커널 evdev 이벤트로 감지한다.

        busy-wait 폴링 대신 select()로 블로킹 대기하여, 버튼이 눌리지 않는
        대기 상황에서 CPU가 깊은 저전력 상태에 진입할 수 있게 한다.
        """
        from evdev import ecodes

        try:
            while not self._stop_event.is_set():
                ready, _, _ = select.select([device.fd], [], [], _STOP_CHECK_TIMEOUT_SEC)
                if device.fd not in ready:
                    continue  # 타임아웃 — stop_event 재확인
                for event in device.read():
                    if (
                        event.type == ecodes.EV_KEY
                        and event.code == ecodes.KEY_POWER
                        and event.value == 1  # key down
                    ):
                        logger.debug("전원버튼 누름 감지")
                        try:
                            on_press()
                        except Exception as exc:
                            logger.warning("on_press 콜백 오류: %s", exc)
        finally:
            try:
                device.ungrab()
            except Exception:
                pass
            device.close()

    def stop(self) -> None:
        """폴링 스레드를 중단하고 grab을 해제한다."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("PowerButtonHandler 종료")
