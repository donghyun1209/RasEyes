//
//  NavigationSession.swift
//  RasEyesNav
//
//  걷는 동안 다음 지시를 하나씩 Pi로 내보낸다.
//

import CoreLocation
import Foundation

/// 경로를 따라가며 턴바이턴 지시를 하나씩 Pi로 보내는 세션.
///
/// 화면의 「첫 단계 전송」 버튼은 지시 하나만 수동으로 보내므로 디버깅용이다.
/// 실제 보행에서는 이 클래스가 위치 갱신을 받아 다음 지시로 넘어간다.
///
/// 문장 조립은 Pi의 `fusion/nav_parser.py` 책임이므로 여기서는 **무엇을 언제
/// 보낼지**만 정한다. Pi가 모르는 코드를 받으면 경고 문구로 발화하지, 직진으로
/// 뭉개지 않는다 — 그래서 `.unknown`(`?`)도 그대로 흘려보낸다.
final class NavigationSession: ObservableObject {

    /// 지금 향하고 있는 지시의 인덱스. 경로를 다 걸으면 `steps.count`가 된다.
    @Published private(set) var currentIndex: Int = 0

    /// 마지막으로 Pi에 보낸 코드. 화면 확인용.
    @Published private(set) var lastSentCode: String?

    /// 안내가 진행 중인지 여부.
    @Published private(set) var isRunning: Bool = false

    /// 지시 지점까지 이 거리(m) 안에 들어오면 한 번 예고한다.
    static let announceRadius: CLLocationDistance = 60

    /// 이 거리(m) 안에 들어오면 그 지시를 통과한 것으로 보고 다음으로 넘어간다.
    static let passRadius: CLLocationDistance = 15

    private var steps: [RouteStep] = []
    private var announcedIndices: Set<Int> = []

    /// 이번 경로의 전체 지시 개수.
    var totalSteps: Int { steps.count }

    /// 경로 안내를 시작한다.
    ///
    /// - Parameter route: 따라갈 경로.
    func start(route: Route) {
        steps = route.steps
        currentIndex = 0
        announcedIndices = []
        lastSentCode = nil
        isRunning = !steps.isEmpty
    }

    /// 경로 안내를 끝낸다.
    func stop() {
        isRunning = false
        steps = []
        announcedIndices = []
        currentIndex = 0
    }

    /// 위치가 갱신될 때마다 호출한다.
    ///
    /// - Parameter location: 현재 좌표.
    /// - Returns: Pi로 보낼 압축 코드. 이번 갱신에 보낼 것이 없으면 `nil`.
    func update(location: CLLocationCoordinate2D) -> String? {
        guard isRunning, currentIndex < steps.count else { return nil }

        let step = steps[currentIndex]

        // 좌표가 없는 스텝은 거리로 판정할 수 없다. 현재 스텝이 되는 즉시 보내고
        // 다음으로 넘긴다 — 조용히 건너뛰면 지시 하나가 통째로 사라진다.
        guard let target = step.coordinate else {
            let code = step.wireFormat
            advance()
            lastSentCode = code
            return code
        }

        let here = CLLocation(latitude: location.latitude, longitude: location.longitude)
        let there = CLLocation(latitude: target.latitude, longitude: target.longitude)
        let distance = here.distance(from: there)

        var code: String?

        // 예고는 지시당 한 번만. 거리는 **다시 잰다** — RouteStep.distanceMeters는
        // 직전 구간의 길이라, 60m 앞에서 그 값을 그대로 보내면 틀린 숫자를 말한다.
        if distance <= Self.announceRadius, !announcedIndices.contains(currentIndex) {
            announcedIndices.insert(currentIndex)
            code = step.wireCode(distanceMeters: Int(distance.rounded()))
        }

        // 지시 지점을 지났으면 다음 지시로 넘어간다.
        if distance <= Self.passRadius {
            advance()
        }

        if let code {
            lastSentCode = code
            return code
        }
        return nil
    }

    private func advance() {
        currentIndex += 1
        if currentIndex >= steps.count {
            isRunning = false
        }
    }
}
