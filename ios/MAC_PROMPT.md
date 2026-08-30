# 맥의 클로드에 붙여넣을 프롬프트

아래 `---` 사이를 통째로 복사해서 **맥에서 실행 중인 클로드 코드**에 붙여넣는다.
저장소를 먼저 맥으로 가져와야 한다 (`git clone` 또는 `git pull`).

---

RasEyes 프로젝트의 iOS 앱(2.2 로드맵 Phase 1)을 맥에서 마무리해줘.
저장소의 `ios/` 폴더에 Xcode 프로젝트가 이미 커밋되어 있고, **리눅스 PC에서 할 수 있는
작업은 2026-08-30에 모두 끝났다.** 남은 건 맥에서만 가능한 것 둘뿐이야.

## 배경 (이미 처리된 것 — 되돌리지 마)

* **위치 권한 문구는 이미 들어가 있다.** 이 프로젝트는 `GENERATE_INFOPLIST_FILE = YES`라
  Info.plist 파일이 없고 `INFOPLIST_KEY_*` 빌드 설정으로 관리돼서,
  `project.pbxproj`에 `INFOPLIST_KEY_NSLocationWhenInUseUsageDescription`을 Debug·Release
  양쪽에 직접 넣었다. Xcode의 Info 탭에서 또 추가하지 마.
* **`SWIFT_OBJC_BRIDGING_HEADER` 설정을 지웠다.** 저장소에 없는 헤더 파일을 가리키고 있어서
  빌드가 즉시 실패하는 상태였어. Objective-C 코드가 없는 Swift 전용 프로젝트니까
  빈 헤더를 만들어서 되살리지 마.
* **TMAP `turnType` 코드표는 공식 문서로 검증을 마쳤다** (`ios/README.md` 부록).

## 할 일 1 — TMAP 앱 키

키는 내가 직접 발급해야 해. 아래 절차를 알려주고 **내가 키를 줄 때까지 기다려줘.**

1. `openapi.sk.com` 가입 (결제 수단 등록은 필요 없다)
2. 앱 등록 → **보행자 경로안내** API 추가
3. 발급된 앱 키 복사

키를 받으면 이렇게 넣어줘:

```bash
cd ios/RasEyesApp
cp Secrets.example.swift RasEyesApp/Secrets.swift
# RasEyesApp/Secrets.swift 의 tmapAppKey 에 키를 넣는다
```

⚠️ **반드시 `cp`(복사)로 해. `mv`(이동)로 옮기면 안 된다.** 이 프로젝트는 Xcode 16의
폴더 동기화 방식(`PBXFileSystemSynchronizedRootGroup`)이라 `RasEyesApp/RasEyesApp/` 안의
`.swift`가 전부 자동으로 빌드에 포함돼. 템플릿과 실제 키 파일이 같은 폴더에 있으면
`enum Secrets`가 두 번 선언돼서 `Invalid redeclaration of 'Secrets'`로 빌드가 깨진다.
템플릿은 반드시 한 단계 위(`ios/RasEyesApp/`)에 그대로 남겨둬.

`Secrets.swift`는 `.gitignore`(`ios/**/Secrets.swift`)에 있으니 커밋되지 않아. **커밋하지 마.**

## 할 일 2 — 빌드하고 시뮬레이터에서 확인

GUI보다 CLI가 오류를 읽기 쉬우니 `xcodebuild`로 해줘.
**이 프로젝트에는 공유 스킴이 없으니 `-scheme`이 아니라 `-target`을 써.**
(`xcodebuild -list`에 스킴이 안 뜨는 게 정상이다. Xcode로 한 번 열면 자동 생성되긴 해.)

```bash
cd ios/RasEyesApp
xcodebuild -project RasEyesApp.xcodeproj -target RasEyesApp \
  -sdk iphonesimulator -configuration Debug \
  -destination 'platform=iOS Simulator,name=iPhone 16' build
```

빌드가 되면 시뮬레이터를 띄우고 **위치를 서울시청으로 지정**한 뒤 실행해줘.
지정하지 않으면 좌표가 영영 `nil`이라 경로 조회까지 못 간다.

```bash
xcrun simctl boot "iPhone 16"          # 이미 켜져 있으면 생략
xcrun simctl location booted set 37.5665,126.9780
xcrun simctl install booted <빌드 산출물 .app 경로>
xcrun simctl launch booted com.donghyun.RasEyesApp
```

화면 확인은 `xcrun simctl io booted screenshot /tmp/shot.png`로 캡처해서 직접 봐줘.

### 완료 판정 체크리스트

넷이 모두 통과하면 Phase 1 완료다.

| 단계 | 기대 결과 |
|---|---|
| 앱 실행 | 위치 권한 팝업이 뜬다 → 허용 |
| 지도 | 파란 점(현재 위치)이 서울에 찍힌다 |
| 검색 | "덕수궁" 입력 후 엔터 → 결과 목록이 나온다 |
| 결과 선택 | 경로 지시 목록 + 총 거리·시간이 뜬다 |

경로 목록의 각 행 앞에 `R|50` 같은 **파란 코드 배지**가 붙어. 이게 Phase 2에서 Pi로
실제 전송할 문자열이야.

### 🔶 주황 배지가 뜨면 꼭 알려줘

배지가 주황색(`?|…`)이면 공식 코드표에 없는 `turnType`이 온 거야. 그 행의 **원문 안내
문구를 그대로 보고해줘.** 코드표에 채워 넣어야 한다.
(단, 공식표의 `1~7`은 "안내 없음"이라 일부러 매핑하지 않았어. 이 대역이면 정상이야.)

## 막혔을 때 — 예상 실패 모드

* **`IPHONEOS_DEPLOYMENT_TARGET = 26.5`보다 낮은 시뮬레이터 런타임만 있다** →
  `xcrun simctl list runtimes`로 확인. 26.5 이상이 없을 때**만** 배포 타겟을 낮춰.
  소스가 쓰는 가장 최신 API가 iOS 17의 `MapCameraPosition`·`UserAnnotation`이라 17.0까지 내려간다.
  런타임이 충분하면 **건드리지 마.**
* **동시성 관련 경고** — `SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor`와
  `CLLocationManagerDelegate`(nonisolated 프로토콜) 조합에서 경고가 날 수 있어.
  언어 모드가 Swift 5.0이라 에러는 아닐 것으로 보는데 확인은 안 됐다. 에러로 막히면
  `LocationManager`의 delegate 메서드에 `nonisolated`를 붙이는 쪽으로 해결하고,
  `@Published` 갱신은 반드시 메인 액터에서 일어나게 유지해줘.
* **위치가 계속 `nil`이고 콘솔에 `[LocationManager] 위치 갱신 실패`만 반복** →
  시뮬레이터 위치를 지정하지 않은 거야. 위의 `simctl location` 명령을 다시 실행.
* **TMAP 401 / 403** → 키가 잘못됐거나 앱에 **보행자 경로안내** API를 추가하지 않은 거야.
* **`Invalid redeclaration of 'Secrets'`** → 템플릿을 `RasEyesApp/RasEyesApp/`으로 옮긴 거야.
  위의 `cp` 주의사항을 다시 봐.
* **한글 권한 문구가 깨져 보인다** → `project.pbxproj`에 UTF-8로 넣었어. Xcode가 저장하면서
  이스케이프 형태로 바꿀 수는 있지만 동작에는 문제 없다.

## 손대지 말 것

* **배포 타겟** — 위 조건에 걸리지 않는 한 `26.5` 그대로. 실기로 쓸 아이폰이 그보다 높다.
* **폴더 동기화 구조** — 소스 파일을 프로젝트 네비게이터에 드래그할 필요 없다.
  `RasEyesApp/RasEyesApp/`에 넣기만 하면 자동 포함돼.
* **Pi의 Piper TTS가 영어 모델인 전제** — 한국어 프로젝트에 영어 TTS라 버그처럼 보이지만
  의도된 선택이야. 그래서 앱은 TMAP의 한국어 원문을 Pi로 보내지 않고 코드로 정규화한다.
* **Phase 2 항목** (BLE 권한 문구, Background Modes, 백그라운드 위치) — 아직 하지 마.
  Phase 2 착수할 때 실제 코드와 같이 넣을 거야.

## 끝나면

`ios/README.md`와 `docs/2.2_ROADMAP.md`의 Phase 1 상태를 완료로 갱신하고,
주황 배지로 발견한 미매핑 `turnType`이 있으면 함께 기록해줘.

---
