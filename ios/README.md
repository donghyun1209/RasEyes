# RasEyesNav — iOS 길안내 앱 (2.2 로드맵 Phase 1)

폰에서 목적지를 검색해 도보 턴바이턴 경로를 뽑고, 이후 Phase 2에서 그 지시를
Pi로 전송한다. **Xcode 프로젝트는 이미 생성되어 커밋되어 있다** — 맥에서는 아래
"남은 할 일"만 하면 빌드된다.

## 왜 MapKit + TMAP 조합인가

| 기능 | 수단 | 한국 | 파리 |
|---|---|:---:|:---:|
| 지도 표시 | MapKit | ✅ | ✅ |
| 현재 위치 | CoreLocation | ✅ | ✅ |
| 목적지 검색 | `MKLocalSearch` | ✅ | ✅ |
| 도보 턴바이턴 | **TMAP 보행자 경로안내 API** | ✅ | ❌ |
| 도보 턴바이턴 | OpenRouteService (미구현) | ⚪ | ✅ |

국내에서 막히는 것은 **경로 계산(`MKDirections`) 하나**다 (지도 데이터 반출 규제).
지도·위치·검색은 MapKit으로 한국에서도 정상 동작하므로, 경로만 `RouteProvider`
프로토콜로 분리해 지역별 구현을 갈아끼운다. Pi 쪽 HAL과 같은 패턴이다.

파리 출국 전에 `ORSRouteProvider`를 추가하고 좌표로 제공자를 고르면 된다.
화면·통신 계층은 프로토콜만 보므로 수정이 필요 없다.

## 폴더 구조

```
ios/
├── README.md
└── RasEyesApp/
    ├── RasEyesApp.xcodeproj/       Xcode 프로젝트
    ├── Secrets.example.swift       ← 키 템플릿 (빌드 제외)
    └── RasEyesApp/                 ← 빌드 대상 폴더
        ├── RasEyesNavApp.swift     앱 진입점
        ├── ContentView.swift       지도 · 검색 · 경로 지시 목록
        ├── LocationManager.swift   CoreLocation 권한 · 좌표
        ├── RouteProvider.swift     제공자 프로토콜 + 정규화 모델
        ├── TmapRouteProvider.swift TMAP GeoJSON 호출 · 파싱
        ├── Assets.xcassets/
        └── Secrets.swift           ← 여기에 만든다 (gitignore)
```

⚠️ 이 프로젝트는 Xcode 16의 **폴더 동기화 방식**이다
(`PBXFileSystemSynchronizedRootGroup`). `RasEyesApp/RasEyesApp/` 안의 `.swift`는
프로젝트 네비게이터에 드래그하지 않아도 **자동으로 빌드에 포함**된다. 결과가 둘이다:

* **새 파일은 그 폴더에 넣기만 하면 된다** (Phase 2 통신 파일도 마찬가지).
* **키 템플릿을 그 폴더에 두면 안 된다.** `Secrets.example.swift`와 `Secrets.swift`가
  같은 `enum Secrets`를 선언하므로 `Invalid redeclaration of 'Secrets'`로 빌드가
  깨진다. 그래서 템플릿만 한 단계 위에 둔다 (2026-08-28에 이 상태였던 것을 고쳤다).

## 맥에서 남은 할 일

### 1. 위치 권한 문구 추가 ⚠️ 필수

아직 들어가 있지 않다. **이게 없으면 권한 요청 팝업이 뜨지 않고 위치가 영영 `nil`이다.**

```
타겟 → Info 탭 → 항목 추가:
Privacy - Location When In Use Usage Description
  → 도보 경로를 안내하기 위해 현재 위치를 사용합니다.
```

### 2. TMAP 앱 키 발급 (결제 수단 등록 불필요)

1. SK open API 포털(`openapi.sk.com`) 가입 — 포털 주소가 바뀌었을 수 있으니
   안 열리면 "TMAP 보행자 경로안내 API"로 검색
2. 앱 등록 → **보행자 경로안내** API 추가
3. `Secrets.example.swift`를 **`RasEyesApp/RasEyesApp/Secrets.swift`로 복사**하고
   발급된 앱 키를 `tmapAppKey`에 붙여넣기 (폴더 동기화라 별도 추가 조작은 없다)

> `Secrets.swift`는 `.gitignore`(`ios/**/Secrets.swift`)에 등록되어 커밋되지 않는다.

### 참고: 손대지 않아도 되는 것

* **배포 타겟은 `IPHONEOS_DEPLOYMENT_TARGET = 26.5` 그대로 둔다.** 실기로 쓸
  아이폰이 그보다 높아 설치에 문제가 없다 (2026-08-28 확인). 낮출 이유가 생기면
  타겟 → General → Minimum Deployments에서 내리면 되고, 소스가 쓰는 가장 최신
  API가 iOS 17의 `MapCameraPosition`·`UserAnnotation`이라 17.0까지 내려간다.
* `SWIFT_VERSION = 5.0` — Swift 6 동시성 경고(`CLLocationManagerDelegate`가
  `@Published`를 갱신하는 지점)는 발생하지 않는다.
* 소스 파일 추가 — 폴더 동기화라 드래그가 필요 없다.

## 동작 확인

시뮬레이터는 위치가 비어 있으므로 먼저 지정한다:

```
시뮬레이터 → Features → Location → Custom Location
  위도 37.5665 / 경도 126.9780   (서울시청)
```

**확인 순서**

| 단계 | 기대 결과 |
|---|---|
| 앱 실행 | 위치 권한 팝업 → 허용 |
| 지도 | 파란 점(현재 위치)이 서울에 찍힘 |
| 검색 | "덕수궁" 입력 후 엔터 → 결과 목록 |
| 결과 선택 | 경로 지시 목록 + 총 거리·시간 |

경로 목록의 각 행 앞에 `R|50` 같은 **파란 코드 배지**가 붙는다. 이게 Phase 2에서
Pi로 실제 전송할 문자열이다. 배지가 **주황색(`?|…`)이면 매핑되지 않은 회전 코드**다.

## 알려진 미확인 사항

* **TMAP `turnType` 코드표가 미검증이다.** `TmapRouteProvider.maneuver(forTurnType:)`의
  숫자 매핑은 공식 문서로 재확인해야 한다. 모르는 코드를 "직진"으로 몰아넣으면 회전을
  놓치고도 조용히 지나가므로, 확신 없는 값은 `.unknown`으로 떨어뜨리고 원문을 화면에
  남겨 두었다. 실측에서 주황 배지가 뜨는 코드를 보고 채워 넣는다.
* 지도에 경로선(폴리라인)을 그리지 않는다. `LineString` 좌표를 버리고 있으므로
  필요해지면 `TmapResponse.Geometry`에서 살린다.
* 백그라운드 위치 추적(로드맵 Phase 2-4)은 아직 붙이지 않았다.

## Phase 2에서 추가할 것 (BLE 확정 — 2026-08-28)

Pi에 온보드 블루투스가 없어 USB 동글을 도입하기로 했다 (Phase 0 결과, `docs/2.2_ROADMAP.md`).
전송은 **BLE GATT**이므로 앱에 다음이 더 필요하다.

* `Privacy - Bluetooth Always Usage Description` (Info)
* Background Modes → **Uses Bluetooth LE accessories** + **Location updates**
  (주머니에 넣고 화면을 꺼도 전송이 이어져야 한다)

## Pi 배포와의 관계

이 폴더는 **Pi로 배포하지 않는다.** `rsync` 제외 목록에 `ios/`가 포함되어 있어야 한다
(`CLAUDE.md` §8).
