"""PowerButtonHandler 회귀 테스트 (2-A-3).

evdev는 Orange Pi 5 전용 lazy import라 PC에는 설치되어 있지 않다. sys.modules에
가짜 evdev 모듈을 주입해 하드웨어 없이 검증한다. 실제 커널 이벤트 대기(select.select)
없이 동기적으로 재현하기 위해, 가짜 device.read()가 이벤트를 반환하면서 동시에
stop_event를 세워 폴링 루프를 1회 처리 후 자연 종료시킨다 — 별도 스레드를 띄우지
않고도 _poll_loop을 직접 호출해 검증할 수 있다 (tests/test_scan.py의 "스레드를
띄우지 않고 메서드를 직접 호출" 관례를 따름).
"""
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import config
from sensor.power_button_handler import PowerButtonHandler


class _FakeEcodes:
    EV_KEY = 1
    KEY_POWER = 116


def _fake_evdev_module(devices):
    """list_devices()가 주어진 (path, name) 목록을 노출하는 가짜 evdev 모듈."""
    fake = MagicMock()
    fake.ecodes = _FakeEcodes
    fake.list_devices.return_value = [d[0] for d in devices]

    device_by_path = {}

    def _input_device(path):
        return device_by_path[path]

    fake.InputDevice.side_effect = _input_device
    return fake, device_by_path


# --- ① grab 실패 시 예외 처리 ---


def test_grab_실패시_RuntimeError를_올리고_device를_닫는다(monkeypatch):
    fake, device_by_path = _fake_evdev_module([("/dev/input/event0", config.POWER_BUTTON_DEVICE_NAME)])
    device = MagicMock()
    device.name = config.POWER_BUTTON_DEVICE_NAME
    device.grab.side_effect = OSError("다른 프로세스가 점유 중")
    device_by_path["/dev/input/event0"] = device
    monkeypatch.setitem(sys.modules, "evdev", fake)

    handler = PowerButtonHandler()
    with pytest.raises(RuntimeError):
        handler.start(lambda: None)
    device.close.assert_called_once()


def test_장치를_찾지_못하면_RuntimeError(monkeypatch):
    fake, device_by_path = _fake_evdev_module([("/dev/input/event0", "some other device")])
    other_device = MagicMock()
    other_device.name = "some other device"
    device_by_path["/dev/input/event0"] = other_device
    monkeypatch.setitem(sys.modules, "evdev", fake)

    handler = PowerButtonHandler()
    with pytest.raises(RuntimeError):
        handler.start(lambda: None)


def test_evdev_미설치시_RuntimeError(monkeypatch):
    # sys.modules["evdev"] = None이면 import evdev가 ImportError를 낸다 (파이썬 관례).
    monkeypatch.setitem(sys.modules, "evdev", None)

    handler = PowerButtonHandler()
    with pytest.raises(RuntimeError):
        handler.start(lambda: None)


# --- ② 콜백 1회 호출 ---
#
# _poll_loop는 "from evdev import ecodes"를 직접 실행하므로, start()를 거치지 않고
# _poll_loop만 단독 호출하는 아래 테스트들도 sys.modules에 가짜 evdev를 심어야 한다.


def test_버튼_다운_이벤트에_콜백이_정확히_한_번_호출된다(monkeypatch):
    calls = []
    device = MagicMock()
    device.fd = 1
    handler_box = {}

    def fake_read():
        # 이벤트를 반환하는 김에 스스로 폴링을 멈춘다 (실제 커널 대기 없이
        # 결정적으로 "1회 처리 후 종료"를 재현하기 위함).
        handler_box["h"]._stop_event.set()
        return [SimpleNamespace(type=_FakeEcodes.EV_KEY, code=_FakeEcodes.KEY_POWER, value=1)]

    device.read.side_effect = fake_read
    monkeypatch.setitem(sys.modules, "evdev", SimpleNamespace(ecodes=_FakeEcodes))
    monkeypatch.setattr(
        "sensor.power_button_handler.select.select", lambda *a, **k: ([device.fd], [], []),
    )
    handler = PowerButtonHandler()
    handler_box["h"] = handler
    handler._poll_loop(lambda: calls.append(1), device)

    assert calls == [1]


def test_KEY_UP_이벤트는_콜백을_호출하지_않는다(monkeypatch):
    calls = []
    device = MagicMock()
    device.fd = 1
    handler_box = {}

    def fake_read():
        handler_box["h"]._stop_event.set()
        return [SimpleNamespace(type=_FakeEcodes.EV_KEY, code=_FakeEcodes.KEY_POWER, value=0)]

    device.read.side_effect = fake_read
    monkeypatch.setitem(sys.modules, "evdev", SimpleNamespace(ecodes=_FakeEcodes))
    monkeypatch.setattr(
        "sensor.power_button_handler.select.select", lambda *a, **k: ([device.fd], [], []),
    )
    handler = PowerButtonHandler()
    handler_box["h"] = handler
    handler._poll_loop(lambda: calls.append(1), device)

    assert calls == []


def test_콜백_예외는_폴링_루프를_죽이지_않는다(monkeypatch):
    """on_press 콜백이 예외를 던져도 _poll_loop는 삼키고 계속 돈다."""
    device = MagicMock()
    device.fd = 1
    handler_box = {}

    def fake_read():
        handler_box["h"]._stop_event.set()
        return [SimpleNamespace(type=_FakeEcodes.EV_KEY, code=_FakeEcodes.KEY_POWER, value=1)]

    device.read.side_effect = fake_read

    def raising_callback():
        raise ValueError("콜백 오류")

    monkeypatch.setitem(sys.modules, "evdev", SimpleNamespace(ecodes=_FakeEcodes))
    monkeypatch.setattr(
        "sensor.power_button_handler.select.select", lambda *a, **k: ([device.fd], [], []),
    )
    handler = PowerButtonHandler()
    handler_box["h"] = handler
    handler._poll_loop(raising_callback, device)  # 예외가 밖으로 새면 테스트 실패


# --- ③ stop()이 grab을 반드시 푸는지 ---


def test_poll_loop_종료시_항상_ungrab_하고_close_한다(monkeypatch):
    handler = PowerButtonHandler()
    handler._stop_event.set()  # 즉시 종료
    device = MagicMock()
    device.fd = 1

    monkeypatch.setitem(sys.modules, "evdev", SimpleNamespace(ecodes=_FakeEcodes))
    monkeypatch.setattr(
        "sensor.power_button_handler.select.select", lambda *a, **k: ([], [], []),
    )
    handler._poll_loop(lambda: None, device)

    device.ungrab.assert_called_once()
    device.close.assert_called_once()


def test_stop은_폴링_스레드를_join하고_정리한다():
    handler = PowerButtonHandler()
    started = threading.Event()

    def fake_loop():
        started.set()
        while not handler._stop_event.is_set():
            time.sleep(0.01)

    handler._thread = threading.Thread(target=fake_loop, daemon=True)
    handler._thread.start()
    assert started.wait(timeout=1.0)

    handler.stop()

    assert handler._thread is None
    assert handler._stop_event.is_set()


def test_stop은_스레드가_없어도_에러_없이_동작한다():
    handler = PowerButtonHandler()
    handler.stop()  # 예외 없이 통과해야 한다
