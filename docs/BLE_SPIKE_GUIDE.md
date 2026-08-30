# Phase 2 최소 기능 검증 (Spike) 테스트 가이드

## 개요
이 가이드는 `docs/2.2_ROADMAP.md`의 **Phase 2 (BLE GATT)** 첫 번째 단계인 '단순 문자열 송수신'을 검증하기 위한 것입니다.

## 1. Orange Pi 5 (BLE 서버) 준비
Orange Pi 5에 SSH로 접속하여 두 개의 터미널 창을 엽니다.

### 터미널 1 (광고 실행)
미리 세팅해 둔 BlueZ 표준 Advertisement 스크립트를 실행합니다. 이 스크립트는 `RasEyes_Nav_Spike`라는 이름으로 기기를 광고합니다.
```bash
python3 scripts/example-advertisement.py
```

### 터미널 2 (GATT 서버 실행)
마찬가지로 수신을 담당할 BlueZ 표준 GATT Server 스크립트를 실행합니다. Test Characteristic(`...abcdef1`)을 통해 쓰기 이벤트를 받습니다.
```bash
python3 scripts/example-gatt-server.py
```

## 2. iOS 앱 (BLE 클라이언트) 테스트
이미 `ios/RasEyesApp/RasEyesApp/BLEManager.swift` 코드를 작성해 두었고, `ContentView.swift` 화면에 테스트 버튼도 달아 두었습니다.

1. **실기기 연결:** iOS 시뮬레이터는 블루투스를 지원하지 않습니다. 반드시 iPhone 실기기를 Mac에 연결하세요.
2. **Xcode 빌드:** Xcode에서 `RasEyesApp`을 열고 실기기를 대상으로 빌드 및 실행(Cmd+R)합니다.
3. 앱이 켜지면 자동으로 주변의 `RasEyes_Nav_Spike` 기기를 스캔하여 연결합니다.
4. 목적지를 검색해 경로 목록을 띄운 뒤, 목록 하단에 **"BLE 연결됨"** 상태가 뜨는지 확인합니다. (권한 팝업이 뜨면 허용해 주세요)
5. **"첫 단계 전송 테스트"** 버튼을 누릅니다.

## 3. 결과 확인
Orange Pi 5의 **터미널 2** 화면에 다음과 같이 수신된 데이터(`R|50` 같은 와이어 포맷)가 출력된다면 Phase 2의 첫 단계(Spike)가 **성공**한 것입니다!
```text
TestCharacteristic Write: dbus.Array([dbus.Byte(82), dbus.Byte(124), dbus.Byte(53), dbus.Byte(48)], signature=dbus.Signature('y'))
```
*(위 예시는 `R|50`의 ASCII 바이트 배열입니다)*

성공이 확인되면, 다음 단계인 `ble_nav_hal.py` 연동(Pi 쪽 메인 루프 통합)을 진행하면 됩니다.
