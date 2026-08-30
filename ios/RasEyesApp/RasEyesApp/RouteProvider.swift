//
//  RouteProvider.swift
//  RasEyesNav
//
//  경로 제공자 추상화 계층.
//

import CoreLocation
import Foundation

/// Pi로 전송할 회전 동작 코드.
///
/// 경로 제공자(TMAP·OpenRouteService 등)마다 안내 문구와 코드 체계가 다르므로
/// 앱에서 이 공통 코드로 정규화한 뒤 Pi에 넘긴다. Pi의 TTS는 영어 모델
/// (`en_US-lessac-medium`)이므로 제공자의 한국어 원문을 그대로 보내지 않는다.
/// 문장 조립은 Pi 쪽 Phase 3의 책임이다.
///
/// rawValue는 BLE로 나가는 실제 바이트다. 서로 접두어가 겹쳐도 무방하지만
/// (`|` 구분자가 있으므로) **중복은 안 된다** — Swift가 컴파일 에러로 잡아준다.
enum ManeuverCode: String {

    // MARK: 회전

    case start = "S"
    case straight = "F"
    case left = "L"
    case right = "R"
    case slightLeft = "SL"
    case slightRight = "SR"
    case sharpLeft = "HL"
    case sharpRight = "HR"
    case uTurn = "U"

    // MARK: 횡단보도

    /// 횡단보도는 방향까지 구분한다. 시각장애인에게 "왼쪽 횡단보도"와 "오른쪽
    /// 횡단보도"는 안전에 직결되는 정보이므로 하나로 뭉개지 않는다.
    /// TMAP은 이를 211~217로 세분해 준다.
    case crosswalk = "X"
    case crosswalkLeft = "XL"
    case crosswalkRight = "XR"
    case crosswalk8 = "X8"
    case crosswalk10 = "X10"
    case crosswalk2 = "X2"
    case crosswalk4 = "X4"

    // MARK: 시설물

    case overpass = "OP"
    case underpass = "UP"
    case stairs = "ST"
    case ramp = "RP"
    case stairsAndRamp = "SP"
    case elevator = "EV"

    // MARK: 기타

    /// 경유지 통과. 현재 앱은 경유지를 넣지 않으므로 실제로는 오지 않는다.
    case waypoint = "W"
    case arrive = "A"
    case unknown = "?"
}

/// 턴바이턴 지시 한 건.
struct RouteStep: Identifiable {
    let id = UUID()

    /// 정규화된 회전 동작.
    let maneuver: ManeuverCode

    /// 이 지시를 수행하기까지 남은 거리(m). 직전 구간의 이동 거리다.
    let distanceMeters: Int

    /// 제공자가 준 원문 안내 문구. 화면 표시와 디버깅에만 쓰고 Pi로 보내지 않는다.
    let rawDescription: String

    /// 지시가 발생하는 지점. 경로 이탈 재계산(파리 도착 후 과제)에 쓴다.
    let coordinate: CLLocationCoordinate2D?

    /// Pi로 보낼 압축 형식 (예: `R|50`). 목록 표시와 수동 전송에 쓴다.
    ///
    /// iOS 기본 MTU(20바이트)를 넘지 않도록 짧게 유지한다.
    var wireFormat: String {
        wireCode(distanceMeters: distanceMeters)
    }

    /// 남은 거리를 다시 재서 만든 압축 코드.
    ///
    /// `distanceMeters`는 **직전 구간의 길이**라, 걸어가면서 예고할 때 그대로
    /// 쓰면 실제 남은 거리와 어긋난다. `NavigationSession`이 현재 위치에서
    /// 다시 잰 값을 넣어 호출한다.
    ///
    /// - Parameter meters: 지시 지점까지 남은 거리(m).
    /// - Returns: `<동작>|<거리>` 형식의 코드.
    func wireCode(distanceMeters meters: Int) -> String {
        "\(maneuver.rawValue)|\(meters)"
    }
}

/// 출발지에서 목적지까지의 도보 경로.
struct Route {
    let steps: [RouteStep]
    let totalDistanceMeters: Int
    let totalSeconds: Int
}

/// 도보 경로 제공자.
///
/// 한국에서는 `TmapRouteProvider`, 프랑스에서는 OpenRouteService 구현을 쓴다.
/// 지역이 바뀌어도 화면·통신 계층은 이 프로토콜만 보므로 수정이 필요 없다.
protocol RouteProvider {
    /// 로그와 화면에 표시할 제공자 이름.
    var name: String { get }

    /// 서비스 가능 지역인지 대략 판정한다.
    ///
    /// - Parameter coordinate: 확인할 좌표.
    /// - Returns: 이 제공자가 해당 좌표를 지원하면 `true`.
    func supports(_ coordinate: CLLocationCoordinate2D) -> Bool

    /// 도보 경로를 조회한다.
    ///
    /// - Parameters:
    ///   - origin: 출발 좌표.
    ///   - destination: 도착 좌표.
    ///   - destinationName: 목적지 표시 이름.
    /// - Returns: 정규화된 경로.
    /// - Throws: 네트워크 오류 또는 응답 파싱 실패.
    func route(from origin: CLLocationCoordinate2D,
               to destination: CLLocationCoordinate2D,
               destinationName: String) async throws -> Route
}

/// 경로 조회 중 발생하는 오류.
enum RouteError: LocalizedError {
    case missingAPIKey
    case outOfServiceArea(provider: String)
    case httpStatus(Int)
    case emptyRoute

    var errorDescription: String? {
        switch self {
        case .missingAPIKey:
            return "API 키가 설정되지 않았습니다. Secrets.swift를 확인하세요."
        case .outOfServiceArea(let provider):
            return "\(provider)의 서비스 지역을 벗어났습니다."
        case .httpStatus(let code):
            return "경로 서버 오류 (HTTP \(code))"
        case .emptyRoute:
            return "경로를 찾지 못했습니다."
        }
    }
}
