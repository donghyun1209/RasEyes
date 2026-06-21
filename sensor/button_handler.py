"""물리 버튼 GPIO 이벤트 핸들러 (Orange Pi 5)."""
import logging
import threading
import time
from typing import Callable, Optional

import config

logger = logging.getLogger(__name__)

_DEBOUNCE_SEC = 0.05   # 50ms 디바운싱
_POLL_INTERVAL_SEC = 0.02   # 20ms 폴링 주기


class ButtonHandler:
    """GPIO 핀에 연결된 물리 버튼 이벤트 핸들러.

    gpiod 라이브러리를 lazy import하므로 PC에서도 임포트 오류 없이 사용 가능.
    on_press 콜백에서 mute 토글, 앱 종료 등 원하는 동작을 구현한다.

    Args:
        pin: 감지할 GPIO 핀 번호 (BCM 기준).
        chip_name: gpiod 칩 이름 (Orange Pi 5 기본값: "gpiochip1").
    """

    def __init__(
        self,
        pin: int = config.GPIO_BUTTON_PIN,
        chip_name: str = "gpiochip1",
    ) -> None:
        self._pin = pin
        self._chip_name = chip_name
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, on_press: Callable[[], None]) -> None:
        """버튼 폴링 daemon 스레드를 시작한다.

        Args:
            on_press: 버튼 누름 감지 시 호출할 콜백 (인자 없음).

        Raises:
            RuntimeError: gpiod 패키지 미설치 시.
        """
        try:
            import gpiod  # lazy import (Orange Pi 5 전용)
        except ImportError as exc:
            raise RuntimeError(
                "gpiod가 필요합니다: pip install gpiod\n"
                "시스템 패키지도 필요합니다: sudo apt install libgpiod-dev libgpiod2"
            ) from exc

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            args=(on_press, gpiod),
            daemon=True,
            name="button-handler",
        )
        self._thread.start()
        logger.info("ButtonHandler 시작 (pin=%d, chip=%s)", self._pin, self._chip_name)

    def _poll_loop(self, on_press: Callable[[], None], gpiod) -> None:
        """버튼 상태를 폴링하며 눌림을 감지한다."""
        try:
            chip = gpiod.Chip(self._chip_name)
            line = chip.get_line(self._pin)
            line.request(consumer="raseyes-button", type=gpiod.LINE_REQ_DIR_IN)
        except Exception as exc:
            logger.error("ButtonHandler GPIO 초기화 실패: %s", exc)
            return

        prev_value = 1  # 풀업 저항 기본값 HIGH
        try:
            while not self._stop_event.is_set():
                value = line.get_value()
                if prev_value == 1 and value == 0:  # falling edge (눌림)
                    time.sleep(_DEBOUNCE_SEC)
                    if line.get_value() == 0:       # 디바운싱 후 재확인
                        logger.debug("버튼 누름 감지 (pin=%d)", self._pin)
                        try:
                            on_press()
                        except Exception as exc:
                            logger.warning("on_press 콜백 오류: %s", exc)
                prev_value = value
                time.sleep(_POLL_INTERVAL_SEC)
        finally:
            line.release()
            chip.close()

    def stop(self) -> None:
        """폴링 스레드를 중단한다."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("ButtonHandler 종료")
