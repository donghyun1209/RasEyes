//
//  TmapRouteProvider.swift
//  RasEyesNav
//
//  TMAP 보행자 경로안내 API 구현 (한국 전용).
//

import CoreLocation
import Foundation

/// TMAP 보행자 경로안내 API 기반 경로 제공자.
///
/// 한국에서 도보 턴바이턴을 제공하는 몇 안 되는 공개 REST API다.
/// SDK 설치가 필요 없고 무료 쿼터에 결제 수단 등록도 요구하지 않는다.
/// 횡단보도·육교·지하보도를 별도 코드로 구분해주므로 시각장애인 안내에 적합하다.
///
/// 서비스 지역이 한국으로 한정되므로 파리에서는 다른 구현으로 교체한다.
struct TmapRouteProvider: RouteProvider {
    let name = "TMAP 보행자"

    private let appKey: String
    private let session: URLSession

    /// 한국 본토를 넉넉히 감싸는 경계 상자. 정밀한 국경선이 아니라
    /// "명백히 해외인 좌표"를 걸러 불필요한 API 호출을 막는 용도다.
    private static let koreaBounds = (
        minLat: 33.0, maxLat: 38.7,
        minLon: 124.5, maxLon: 132.0
    )

    /// - Parameters:
    ///   - appKey: TMAP Open API에서 발급받은 앱 키.
    ///   - session: 테스트에서 교체할 수 있도록 주입받는다.
    init(appKey: String, session: URLSession = .shared) {
        self.appKey = appKey
        self.session = session
    }

    func supports(_ coordinate: CLLocationCoordinate2D) -> Bool {
        let b = Self.koreaBounds
        return coordinate.latitude >= b.minLat && coordinate.latitude <= b.maxLat
            && coordinate.longitude >= b.minLon && coordinate.longitude <= b.maxLon
    }

    func route(from origin: CLLocationCoordinate2D,
               to destination: CLLocationCoordinate2D,
               destinationName: String) async throws -> Route {
        guard !appKey.isEmpty else { throw RouteError.missingAPIKey }
        guard supports(origin), supports(destination) else {
            throw RouteError.outOfServiceArea(provider: name)
        }

        var request = URLRequest(
            url: URL(string: "https://apis.openapi.sk.com/tmap/routes/pedestrian?version=1")!
        )
        request.httpMethod = "POST"
        request.setValue(appKey, forHTTPHeaderField: "appKey")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "startX": origin.longitude,
            "startY": origin.latitude,
            "endX": destination.longitude,
            "endY": destination.latitude,
            "reqCoordType": "WGS84GEO",
            "resCoordType": "WGS84GEO",
            "startName": "출발",
            "endName": destinationName.isEmpty ? "도착" : destinationName,
            "searchOption": "0"
        ])

        let (data, response) = try await session.data(for: request)
        if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            throw RouteError.httpStatus(http.statusCode)
        }

        let decoded = try JSONDecoder().decode(TmapResponse.self, from: data)
        return try Self.normalize(decoded)
    }

    // MARK: - 응답 정규화

    /// GeoJSON 응답을 `Route`로 변환한다.
    ///
    /// TMAP은 회전 지점(`Point`)과 이동 구간(`LineString`)을 번갈아 돌려준다.
    /// "50m 앞 우회전"을 만들려면 회전 지점 **직전** 구간의 거리를 그 지점에
    /// 붙여야 하므로, 구간 거리를 누적해 두었다가 다음 지점에서 소비한다.
    private static func normalize(_ response: TmapResponse) throws -> Route {
        var steps: [RouteStep] = []
        var pendingDistance = 0
        var totalDistance = 0
        var totalSeconds = 0

        for feature in response.features {
            let props = feature.properties
            totalDistance = props.totalDistance ?? totalDistance
            totalSeconds = props.totalTime ?? totalSeconds

            switch feature.geometry {
            case .point(let coordinate):
                steps.append(RouteStep(
                    maneuver: maneuver(forTurnType: props.turnType),
                    distanceMeters: pendingDistance,
                    rawDescription: props.description ?? "",
                    coordinate: coordinate
                ))
                pendingDistance = 0

            case .line:
                pendingDistance += props.distance ?? 0
            }
        }

        guard !steps.isEmpty else { throw RouteError.emptyRoute }
        return Route(steps: steps,
                     totalDistanceMeters: totalDistance,
                     totalSeconds: totalSeconds)
    }

    /// TMAP `turnType` 코드를 공통 `ManeuverCode`로 옮긴다.
    ///
    /// 코드표는 SK open API 공식 문서(경로안내 샘플예제)로 검증했다 — 2026-08-30.
    /// https://tmap-skopenapi.readme.io/reference/경로안내-샘플예제
    ///
    /// 그 과정에서 오류 세 건을 바로잡았다: `128`은 에스컬레이터가 아니라 **경사로**이고,
    /// 엘리베이터로 적어 두었던 `130`은 **표에 없는 코드**이며, 실제 엘리베이터는 `218`인데
    /// 매핑에서 빠져 있었다. 시각장애인에게 경사로를 에스컬레이터로 안내하면 틀린 정보다.
    ///
    /// 표에 없는 코드는 계속 `.unknown`으로 떨어뜨리고 원문(`rawDescription`)을 화면에
    /// 남긴다. 모르는 코드를 임의로 "직진"에 몰아넣으면 회전을 놓치고도 조용히 지나간다.
    private static func maneuver(forTurnType turnType: Int?) -> ManeuverCode {
        guard let turnType else { return .unknown }
        switch turnType {
        // 회전
        case 11, 233: return .straight  // 233 = 직진 임시
        case 12: return .left
        case 13: return .right
        case 14: return .uTurn
        case 16: return .sharpLeft     // 8시 방향
        case 17: return .slightLeft    // 10시 방향
        case 18: return .slightRight   // 2시 방향
        case 19: return .sharpRight    // 4시 방향

        // 시설물
        case 125: return .overpass      // 육교
        case 126: return .underpass     // 지하보도
        case 127: return .stairs        // 계단 진입
        case 128: return .ramp          // 경사로 진입
        case 129: return .stairsAndRamp // 계단+경사로 진입
        case 218: return .elevator      // 엘리베이터

        // 횡단보도 (방향 구분)
        case 211: return .crosswalk
        case 212: return .crosswalkLeft
        case 213: return .crosswalkRight
        case 214: return .crosswalk8
        case 215: return .crosswalk10
        case 216: return .crosswalk2
        case 217: return .crosswalk4

        // 시종점·경유지
        case 200: return .start
        case 201: return .arrive
        case 184...189: return .waypoint

        // 공식표의 1~7은 "안내 없음"이다. `Point` feature로 실제 오는지 확인되지
        // 않아 매핑하지 않는다 — 주황 배지로 뜨면 놓친 회전이 아니라 이 대역일 수 있다.
        default: return .unknown
        }
    }
}

// MARK: - GeoJSON 디코딩

/// TMAP 보행자 경로 응답 (GeoJSON FeatureCollection).
private struct TmapResponse: Decodable {
    let features: [Feature]

    struct Feature: Decodable {
        let geometry: Geometry
        let properties: Properties
    }

    struct Properties: Decodable {
        let turnType: Int?
        let description: String?
        let distance: Int?
        let totalDistance: Int?
        let totalTime: Int?
    }

    /// `Point`의 좌표만 필요하고 `LineString`의 폴리라인은 쓰지 않으므로
    /// 후자는 좌표를 버린다 (지도에 경로선을 그릴 때 확장한다).
    enum Geometry: Decodable {
        case point(CLLocationCoordinate2D)
        case line

        private enum CodingKeys: String, CodingKey {
            case type, coordinates
        }

        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            let type = try container.decode(String.self, forKey: .type)
            guard type == "Point" else {
                self = .line
                return
            }
            let coordinates = try container.decode([Double].self, forKey: .coordinates)
            guard coordinates.count >= 2 else {
                self = .line
                return
            }
            // GeoJSON은 [경도, 위도] 순서다.
            self = .point(CLLocationCoordinate2D(latitude: coordinates[1],
                                                 longitude: coordinates[0]))
        }
    }
}
