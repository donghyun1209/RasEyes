//
//  LocationManager.swift
//  RasEyesNav
//
//  CoreLocation 래퍼.
//

import CoreLocation
import Foundation

/// 위치 권한과 현재 좌표를 관리한다.
///
/// 경로 조회의 출발점을 제공하고, 이후 Phase 2에서 경로 이탈 판정의 기준이 된다.
/// 화면이 꺼진 상태에서도 추적이 필요하므로(로드맵 Phase 2-4) 백그라운드 권한을
/// 붙일 자리를 남겨둔다 — 지금은 사용 중 권한만 요청한다.
final class LocationManager: NSObject, ObservableObject {

    /// 마지막으로 수신한 좌표. 아직 한 번도 못 받았으면 `nil`.
    @Published private(set) var coordinate: CLLocationCoordinate2D?

    /// 현재 위치 권한 상태.
    @Published private(set) var authorization: CLAuthorizationStatus

    private let manager = CLLocationManager()

    override init() {
        authorization = manager.authorizationStatus
        super.init()
        manager.delegate = self
        // 보행 안내 정확도. 배터리 소모가 크므로 Phase 4 실측 후 조정한다.
        manager.desiredAccuracy = kCLLocationAccuracyBestForNavigation
        manager.distanceFilter = 5
    }

    /// 사용 중 위치 권한을 요청한다.
    func requestPermission() {
        manager.requestWhenInUseAuthorization()
    }

    /// 위치 추적을 시작한다. 권한이 없으면 아무 일도 하지 않는다.
    func start() {
        guard authorization == .authorizedWhenInUse
                || authorization == .authorizedAlways else { return }
        manager.startUpdatingLocation()
    }

    /// 위치 추적을 멈춘다.
    func stop() {
        manager.stopUpdatingLocation()
    }
}

extension LocationManager: CLLocationManagerDelegate {

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        authorization = manager.authorizationStatus
        start()
    }

    func locationManager(_ manager: CLLocationManager,
                         didUpdateLocations locations: [CLLocation]) {
        guard let latest = locations.last else { return }
        coordinate = latest.coordinate
    }

    func locationManager(_ manager: CLLocationManager,
                         didFailWithError error: Error) {
        // 위치 실패는 치명적이지 않다 — 다음 갱신을 기다린다.
        // 시뮬레이터에서는 위치를 지정하지 않으면 항상 여기로 떨어진다.
        print("[LocationManager] 위치 갱신 실패: \(error.localizedDescription)")
    }
}
