"""물리 버튼 GPIO 이벤트 핸들러 (Orange Pi 5)."""
import datetime
import logging
import threading
from typing import Callable, Optional

import config

logger = logging.getLogger(__name__)

_DEBOUNCE_SEC = 0.05
_STOP_CHECK_TIMEOUT_SEC = 0.5  # wait_edge_events 블로킹 대기 상한 (stop_event 확인 주기)


class ButtonHandler:
    """GPIO 핀에 연결된 물리 버튼 이벤트 핸들러.

    gpiod 2.x API 기반. lazy import이므로 PC에서도 임포트 오류 없이 사용 가능.
    on_press 콜백에서 mute 토글, 앱 종료 등 원하는 동작을 구현한다.

    Args:
        pin: 감지할 GPIO 핀 번호 (gpiochip 내 offset).
        chip_path: gpiod 칩 장치 경로 (gpiod 2.x는 전체 경로 필요).
    """

    def __init__(
        self,
        pin: int = config.GPIO_BUTTON_PIN,
        chip_path: str = config.GPIO_CHIP_PATH,
    ) -> None:
        self._pin = pin
        self._chip_path = chip_path
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
        logger.info("ButtonHandler 시작 (pin=%d, chip=%s)", self._pin, self._chip_path)

    def _poll_loop(self, on_press: Callable[[], None], gpiod) -> None:
        """버튼 눌림을 커널 edge 이벤트로 감지한다 (gpiod 2.x API).

        busy-wait 폴링 대신 wait_edge_events()로 블로킹 대기하여, 버튼이
        눌리지 않는 대기 상황에서 CPU가 깊은 저전력 상태에 진입할 수 있게 한다.
        디바운스는 커널(debounce_period)에 위임한다.
        """
        request = None
        try:
            try:
                request = gpiod.request_lines(
                    self._chip_path,
                    consumer="raseyes-button",
                    config={self._pin: gpiod.LineSettings(
                        direction=gpiod.line.Direction.INPUT,
                        edge_detection=gpiod.line.Edge.FALLING,
                        debounce_period=datetime.timedelta(seconds=_DEBOUNCE_SEC),
                    )},
                )
            except Exception as exc:
                logger.error("ButtonHandler GPIO 초기화 실패: %s", exc)
                return

            timeout = datetime.timedelta(seconds=_STOP_CHECK_TIMEOUT_SEC)
            while not self._stop_event.is_set():
                if not request.wait_edge_events(timeout):
                    continue  # 타임아웃 — stop_event 재확인
                for event in request.read_edge_events():
                    if event.event_type == gpiod.EdgeEvent.Type.FALLING_EDGE:
                        logger.debug("버튼 누름 감지 (pin=%d)", self._pin)
                        try:
                            on_press()
                        except Exception as exc:
                            logger.warning("on_press 콜백 오류: %s", exc)
        finally:
            if request is not None:
                request.release()

    def stop(self) -> None:
        """폴링 스레드를 중단한다."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("ButtonHandler 종료")
