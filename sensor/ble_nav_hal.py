"""BLE GATT 서버를 통한 경로 안내(내비게이션) 수신 HAL."""

import array
import logging
import queue
import threading
from typing import Optional

try:
    import dbus
    import dbus.exceptions
    import dbus.mainloop.glib
    import dbus.service
    from gi.repository import GLib
except ImportError:
    # PC 테스트 환경에서는 dbus-python이 없을 수 있으므로 예외 무시
    pass

from sensor.interface import BaseNavHAL

logger = logging.getLogger(__name__)

BLUEZ_SERVICE_NAME = 'org.bluez'
LE_ADVERTISING_MANAGER_IFACE = 'org.bluez.LEAdvertisingManager1'
LE_ADVERTISEMENT_IFACE = 'org.bluez.LEAdvertisement1'
GATT_MANAGER_IFACE = 'org.bluez.GattManager1'
GATT_SERVICE_IFACE = 'org.bluez.GattService1'
GATT_CHRC_IFACE = 'org.bluez.GattCharacteristic1'
DBUS_OM_IFACE = 'org.freedesktop.DBus.ObjectManager'
DBUS_PROP_IFACE = 'org.freedesktop.DBus.Properties'


class InvalidArgsException(dbus.exceptions.DBusException):
    _dbus_error_name = 'org.freedesktop.DBus.Error.InvalidArgs'

class NotSupportedException(dbus.exceptions.DBusException):
    _dbus_error_name = 'org.bluez.Error.NotSupported'

class NotPermittedException(dbus.exceptions.DBusException):
    _dbus_error_name = 'org.bluez.Error.NotPermitted'


class Advertisement(dbus.service.Object):
    PATH_BASE = '/org/bluez/raseyes/advertisement'

    def __init__(self, bus, index, advertising_type):
        self.path = self.PATH_BASE + str(index)
        self.bus = bus
        self.ad_type = advertising_type
        self.service_uuids = None
        self.local_name = None
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        properties = dict()
        properties['Type'] = self.ad_type
        if self.service_uuids is not None:
            properties['ServiceUUIDs'] = dbus.Array(self.service_uuids, signature='s')
        if self.local_name is not None:
            properties['LocalName'] = dbus.String(self.local_name)
        return {LE_ADVERTISEMENT_IFACE: properties}

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(DBUS_PROP_IFACE, in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != LE_ADVERTISEMENT_IFACE:
            raise InvalidArgsException()
        return self.get_properties()[LE_ADVERTISEMENT_IFACE]

    @dbus.service.method(LE_ADVERTISEMENT_IFACE, in_signature='', out_signature='')
    def Release(self):
        logger.info(f"Advertisement {self.path} released")


class RasEyesAdvertisement(Advertisement):
    def __init__(self, bus, index, service_uuid):
        Advertisement.__init__(self, bus, index, 'peripheral')
        self.service_uuids = [service_uuid]
        self.local_name = 'RasEyes'


class Service(dbus.service.Object):
    PATH_BASE = '/org/bluez/raseyes/service'

    def __init__(self, bus, index, uuid, primary):
        self.path = self.PATH_BASE + str(index)
        self.bus = bus
        self.uuid = uuid
        self.primary = primary
        self.characteristics = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        return {
            GATT_SERVICE_IFACE: {
                'UUID': self.uuid,
                'Primary': self.primary,
                'Characteristics': dbus.Array([c.get_path() for c in self.characteristics], signature='o')
            }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(DBUS_PROP_IFACE, in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != GATT_SERVICE_IFACE:
            raise InvalidArgsException()
        return self.get_properties()[GATT_SERVICE_IFACE]


class Characteristic(dbus.service.Object):
    def __init__(self, bus, index, uuid, flags, service):
        self.path = service.path + '/char' + str(index)
        self.bus = bus
        self.uuid = uuid
        self.service = service
        self.flags = flags
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        return {
            GATT_CHRC_IFACE: {
                'Service': self.service.get_path(),
                'UUID': self.uuid,
                'Flags': self.flags,
                'Descriptors': dbus.Array([], signature='o')
            }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(DBUS_PROP_IFACE, in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != GATT_CHRC_IFACE:
            raise InvalidArgsException()
        return self.get_properties()[GATT_CHRC_IFACE]


class NavCharacteristic(Characteristic):
    def __init__(self, bus, index, service, uuid, nav_queue: queue.Queue):
        Characteristic.__init__(self, bus, index, uuid, ['write', 'write-without-response'], service)
        self.nav_queue = nav_queue

    @dbus.service.method(GATT_CHRC_IFACE, in_signature='a{sv}', out_signature='ay')
    def ReadValue(self, options):
        raise NotSupportedException()

    @dbus.service.method(GATT_CHRC_IFACE, in_signature='aya{sv}', out_signature='')
    def WriteValue(self, value, options):
        try:
            val_str = bytes(value).decode('utf-8')
            logger.info(f"BLE Received: {val_str}")
            self.nav_queue.put(val_str)
        except Exception as e:
            logger.error(f"BLE Parse Error: {e}")


class Application(dbus.service.Object):
    def __init__(self, bus):
        self.path = '/'
        self.services = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service(self, service):
        self.services.append(service)

    @dbus.service.method(DBUS_OM_IFACE, out_signature='a{oa{sa{sv}}}')
    def GetManagedObjects(self):
        response = {}
        for service in self.services:
            response[service.get_path()] = service.get_properties()
            for chrc in service.characteristics:
                response[chrc.get_path()] = chrc.get_properties()
        return response


class BleNavHAL(BaseNavHAL):
    """DBus 기반 BLE GATT 네비게이션 센서.
    
    iOS 앱에서 보내는 경로 안내 문자열을 수신한다.
    """

    def __init__(self) -> None:
        self.service_uuid = "12345678-1234-5678-1234-56789abcdef0"
        self.char_uuid = "12345678-1234-5678-1234-56789abcdef1"
        self.nav_queue: queue.Queue[str] = queue.Queue()
        self.mainloop = None
        self.thread = None
        self._running = False
        
        self.bus = None
        self.ad_manager = None
        self.gatt_manager = None
        self.app = None
        self.ad = None

    def _find_adapter(self, bus):
        om = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, '/'), DBUS_OM_IFACE)
        objects = om.GetManagedObjects()
        for path, interfaces in objects.items():
            if LE_ADVERTISING_MANAGER_IFACE in interfaces and GATT_MANAGER_IFACE in interfaces:
                return path
        return None

    def _register_app_cb(self):
        logger.info("GATT Application registered")

    def _register_app_error_cb(self, error):
        logger.error(f"Failed to register GATT application: {str(error)}")
        self.stop()

    def _register_ad_cb(self):
        logger.info("Advertisement registered")

    def _register_ad_error_cb(self, error):
        logger.error(f"Failed to register advertisement: {str(error)}")
        self.stop()

    def _run_mainloop(self):
        try:
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self.bus = dbus.SystemBus()
            adapter_path = self._find_adapter(self.bus)
            if not adapter_path:
                logger.error("LEAdvertisingManager1 or GattManager1 interface not found")
                return

            adapter_obj = self.bus.get_object(BLUEZ_SERVICE_NAME, adapter_path)
            self.ad_manager = dbus.Interface(adapter_obj, LE_ADVERTISING_MANAGER_IFACE)
            self.gatt_manager = dbus.Interface(adapter_obj, GATT_MANAGER_IFACE)

            self.app = Application(self.bus)
            service = Service(self.bus, 0, self.service_uuid, True)
            char = NavCharacteristic(self.bus, 0, service, self.char_uuid, self.nav_queue)
            service.characteristics.append(char)
            self.app.add_service(service)

            self.ad = RasEyesAdvertisement(self.bus, 0, self.service_uuid)

            self.gatt_manager.RegisterApplication(
                self.app.get_path(), {},
                reply_handler=self._register_app_cb,
                error_handler=self._register_app_error_cb
            )

            self.ad_manager.RegisterAdvertisement(
                self.ad.get_path(), {},
                reply_handler=self._register_ad_cb,
                error_handler=self._register_ad_error_cb
            )

            self.mainloop = GLib.MainLoop()
            logger.info("Starting BLE GLib MainLoop")
            self.mainloop.run()
        except Exception as e:
            logger.error(f"BLE MainLoop exception: {e}")
        finally:
            logger.info("BLE MainLoop exited")
            self._running = False

    def start(self) -> None:
        if self._running:
            return
        logger.info("Starting BleNavHAL")
        self._running = True
        self.thread = threading.Thread(target=self._run_mainloop, daemon=True, name="BleNavThread")
        self.thread.start()

    def get_latest_instruction(self) -> Optional[str]:
        if not self._running:
            return None
            
        try:
            # 큐의 최신 값을 가져오기 위해 큐를 다 비운다.
            latest = None
            while True:
                latest = self.nav_queue.get_nowait()
        except queue.Empty:
            return latest

    def stop(self) -> None:
        logger.info("Stopping BleNavHAL")
        if self.ad_manager and self.ad:
            try:
                self.ad_manager.UnregisterAdvertisement(self.ad.get_path())
            except Exception as e:
                logger.warning(f"Error unregistering advertisement: {e}")
                
        if self.gatt_manager and self.app:
            try:
                self.gatt_manager.UnregisterApplication(self.app.get_path())
            except Exception as e:
                logger.warning(f"Error unregistering GATT application: {e}")

        if self.mainloop:
            self.mainloop.quit()
            
        self._running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
