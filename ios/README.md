# RasEyesNav — iOS 길안내 앱 (2.2 로드맵 Phase 1)

폰에서 목적지를 검색해 도보 턴바이턴 경로를 뽑고, 이후 Phase 2에서 그 지시를
Pi로 전송한다. 이 폴더의 `Sources/`는 **Xcode 프로젝트가 아니라 소스 파일 모음**이다
— `.xcodeproj`는 맥에서 생성한 뒤 이 파일들을 넣는다.

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

## 맥에서 할 일 (최초 1회, 약 10분)

### 1. Xcode 프로젝트 생성

```
Xcode → File → New → Project → iOS → App
  Product Name : RasEyesNav
  Interface    : SwiftUI
  Language     : Swift
  저장 위치     : <repo>/ios/
```

`ios/RasEyesNav/` 가 새로 생긴다 (`Sources/`와 별개다).

### 2. 소스 파일 교체

1. Xcode가 자동 생성한 `ContentView.swift`, `RasEyesNavApp.swift`를 **Move to Trash**
2. `ios/Sources/`의 `.swift` 5개를 Xcode 프로젝트 네비게이터로 **드래그**
   (`Copy items if needed` 체크, `Secrets.example.swift`는 제외)
3. `Secrets.example.swift`를 `Secrets.swift`로 복사해 프로젝트에 추가하고 키를 채운다
   (`Secrets.swift`는 `.gitignore`에 등록되어 커밋되지 않는다)

### 3. 위치 권한 문구 추가

타겟 → Info 탭 → 항목 추가:

```
Privacy - Location When In Use Usage Description
  → 도보 경로를 안내하기 위해 현재 위치를 사용합니다.
```

이게 없으면 권한 요청 팝업이 뜨지 않고 위치가 영영 `nil`이다.

### 4. TMAP 앱 키 발급 (결제 수단 등록 불필요)

1. SK open API 포털(`openapi.sk.com`) 가입 — 포털 주소가 바뀌었을 수 있으니
   안 열리면 "TMAP 보행자 경로안내 API"로 검색
2. 앱 등록 → **보행자 경로안내** API 추가
3. 발급된 앱 키를 `Secrets.swift`의 `tmapAppKey`에 붙여넣기

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

## Pi 배포와의 관계

이 폴더는 **Pi로 배포하지 않는다.** `rsync` 제외 목록에 `ios/`가 포함되어 있어야 한다
(`CLAUDE.md` §8).
