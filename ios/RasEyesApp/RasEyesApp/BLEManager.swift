import CoreBluetooth
import Combine

/// BLE 송수신을 담당하는 관리자 클래스
class BLEManager: NSObject, ObservableObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    @Published var isConnected: Bool = false
    @Published var receivedMessages: [String] = [] // Spike 검증용

    private var centralManager: CBCentralManager!
    private var connectedPeripheral: CBPeripheral?
    private var writeCharacteristic: CBCharacteristic?

    // Pi 쪽에 띄울 서비스/특성 UUID (임의 설정)
    let serviceUUID = CBUUID(string: "12345678-1234-5678-1234-56789abcdef0")
    let charUUID = CBUUID(string: "12345678-1234-5678-1234-56789abcdef1")

    override init() {
        super.init()
        centralManager = CBCentralManager(delegate: self, queue: nil)
    }

    func startScanning() {
        guard centralManager.state == .poweredOn else { return }
        centralManager.scanForPeripherals(withServices: [serviceUUID], options: nil)
        print("BLE: 스캔 시작...")
    }

    func stopScanning() {
        centralManager.stopScan()
        print("BLE: 스캔 중지")
    }

    func sendCode(_ code: String) {
        guard let peripheral = connectedPeripheral,
              let characteristic = writeCharacteristic,
              let data = code.data(using: .utf8) else {
            print("BLE: 전송 실패 (연결 안됨 또는 특성 없음)")
            return
        }

        // WriteWithoutResponse를 사용할지 Write를 사용할지는 Pi 서버 설정에 따라 다름
        peripheral.writeValue(data, for: characteristic, type: .withResponse)
        print("BLE: 전송 완료 -> \(code)")
    }

    // MARK: - CBCentralManagerDelegate
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        if central.state == .poweredOn {
            startScanning()
        } else {
            isConnected = false
        }
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String : Any], rssi RSSI: NSNumber) {
        print("BLE: 기기 발견 - \(peripheral.name ?? "Unknown")")
        connectedPeripheral = peripheral
        centralManager.stopScan()
        centralManager.connect(peripheral, options: nil)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        print("BLE: 기기 연결됨!")
        isConnected = true
        peripheral.delegate = self
        peripheral.discoverServices([serviceUUID])
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        print("BLE: 연결 끊김")
        isConnected = false
        writeCharacteristic = nil
        // 재연결 시도
        startScanning()
    }

    // MARK: - CBPeripheralDelegate
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard let services = peripheral.services else { return }
        for service in services {
            peripheral.discoverCharacteristics([charUUID], for: service)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard let characteristics = service.characteristics else { return }
        for characteristic in characteristics {
            if characteristic.uuid == charUUID {
                writeCharacteristic = characteristic
                print("BLE: Write 특성 찾음!")
            }
        }
    }
}
