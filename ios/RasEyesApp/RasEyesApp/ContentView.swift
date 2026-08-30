//
//  ContentView.swift
//  RasEyesNav
//
//  지도 · 목적지 검색 · 경로 지시 목록.
//

import Combine
import MapKit
import SwiftUI

/// Phase 1 메인 화면.
///
/// 지도와 현재 위치는 MapKit으로, 장소 검색은 `MKLocalSearch`로 처리한다.
/// 둘 다 한국에서 정상 동작한다 — 국내에서 막히는 것은 `MKDirections`(경로)뿐이라
/// 경로만 `RouteProvider`로 분리해 지역별 구현을 갈아끼운다.
struct ContentView: View {
    @StateObject private var location = LocationManager()

    @State private var camera: MapCameraPosition = .userLocation(fallback: .automatic)
    @State private var query = ""
    @State private var searchResults: [MKMapItem] = []
    @State private var destination: MKMapItem?
    @State private var route: Route?
    @State private var errorMessage: String?
    @State private var isLoading = false

    @StateObject private var ble = BLEManager()
    @StateObject private var session = NavigationSession()

    /// 지금은 한국 전용 제공자 하나만 쓴다.
    /// 파리 출국 전에 좌표로 제공자를 고르는 분기를 추가한다.
    private let provider: RouteProvider = TmapRouteProvider(appKey: Secrets.tmapAppKey)

    var body: some View {
        VStack(spacing: 0) {
            mapView
            searchBar
            Divider()
            resultPane
        }
        .task {
            location.requestPermission()
            location.start()
        }
        .onReceive(location.$coordinate) { coordinate in
            // ⚠ onChange(of:)로는 안 된다 — CLLocationCoordinate2D는 Equatable이
            // 아니라 컴파일이 실패한다. Published 퍼블리셔를 직접 구독한다.
            guard let coordinate else { return }
            if let code = session.update(location: coordinate) {
                ble.sendCode(code)
            }
        }
        .alert("오류", isPresented: .constant(errorMessage != nil)) {
            Button("확인") { errorMessage = nil }
        } message: {
            Text(errorMessage ?? "")
        }
    }

    // MARK: - 지도

    private var mapView: some View {
        Map(position: $camera) {
            UserAnnotation()
            if let destination {
                Marker(destination.name ?? "목적지",
                       coordinate: destination.placemark.coordinate)
                    .tint(.red)
            }
        }
        .mapControls {
            MapUserLocationButton()
            MapCompass()
        }
        .frame(maxHeight: .infinity)
    }

    // MARK: - 검색

    private var searchBar: some View {
        HStack {
            TextField("목적지 검색", text: $query)
                .textFieldStyle(.roundedBorder)
                .submitLabel(.search)
                .onSubmit { Task { await search() } }

            if isLoading {
                ProgressView()
            }
        }
        .padding(12)
    }

    // MARK: - 결과

    @ViewBuilder
    private var resultPane: some View {
        if let route {
            routeList(route)
        } else if !searchResults.isEmpty {
            searchResultList
        } else {
            statusView
        }
    }

    private var searchResultList: some View {
        List(searchResults, id: \.self) { item in
            Button {
                destination = item
                Task { await requestRoute(to: item) }
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.name ?? "이름 없음")
                        .font(.body)
                    if let address = item.placemark.title {
                        Text(address)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .listStyle(.plain)
        .frame(height: 240)
    }

    /// 경로 지시 목록.
    ///
    /// 각 행에 Pi로 보낼 압축 코드(`wireFormat`)를 함께 띄운다. Phase 2를 붙이기
    /// 전에 어떤 문자열이 실제로 나가는지 눈으로 확인하기 위한 것이다.
    private func routeList(_ route: Route) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("총 \(route.totalDistanceMeters)m · 약 \(route.totalSeconds / 60)분")
                    .font(.subheadline.bold())
                Spacer()
                Button("초기화") {
                    session.stop()
                    self.route = nil
                    destination = nil
                    query = ""
                }
                .font(.caption)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)

            // BLE 상태 · 안내 제어
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Circle()
                        .fill(ble.isConnected ? Color.green : Color.red)
                        .frame(width: 10, height: 10)
                    Text(ble.isConnected ? "BLE 연결됨" : "BLE 대기 중")
                        .font(.caption)

                    Spacer()

                    // 걷지 않고 확인하는 수단 — 디버깅용으로 남겨둔다.
                    Button("첫 단계 전송") {
                        if let firstStep = route.steps.first {
                            ble.sendCode(firstStep.wireFormat)
                        }
                    }
                    .font(.caption)
                    .buttonStyle(.bordered)
                    .disabled(!ble.isConnected)

                    Button(session.isRunning ? "안내 중지" : "안내 시작") {
                        if session.isRunning {
                            session.stop()
                        } else {
                            session.start(route: route)
                        }
                    }
                    .font(.caption)
                    .buttonStyle(.borderedProminent)
                    .disabled(!ble.isConnected)
                }

                if session.isRunning {
                    Text(progressText)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 8)

            List(route.steps) { step in
                HStack(alignment: .top) {
                    Text(step.wireFormat)
                        .font(.system(.caption, design: .monospaced))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(step.maneuver == .unknown ? .orange.opacity(0.3)
                                                              : .blue.opacity(0.15))
                        .clipShape(RoundedRectangle(cornerRadius: 4))

                    Text(step.rawDescription)
                        .font(.callout)
                }
            }
            .listStyle(.plain)
        }
        .frame(height: 260)
    }

    private var statusView: some View {
        VStack(spacing: 6) {
            if location.authorization == .denied || location.authorization == .restricted {
                Text("위치 권한이 없습니다. 설정에서 허용해 주세요.")
            } else if location.coordinate == nil {
                Text("현재 위치를 받는 중…")
            } else {
                Text("목적지를 검색하세요.")
            }
            Text("경로 제공자: \(provider.name)")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .font(.footnote)
        .foregroundStyle(.secondary)
        .frame(height: 100)
    }

    /// 안내 진행 상황 한 줄 요약.
    private var progressText: String {
        let position = min(session.currentIndex + 1, max(session.totalSteps, 1))
        var text = "안내 중 · 지시 \(position)/\(session.totalSteps)"
        if let code = session.lastSentCode {
            text += " · 최근 전송 \(code)"
        }
        return text
    }

    // MARK: - 동작

    /// 현재 위치 주변에서 목적지를 검색한다.
    private func search() async {
        guard !query.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        route = nil
        isLoading = true
        defer { isLoading = false }

        let request = MKLocalSearch.Request()
        request.naturalLanguageQuery = query
        if let coordinate = location.coordinate {
            request.region = MKCoordinateRegion(center: coordinate,
                                                latitudinalMeters: 5_000,
                                                longitudinalMeters: 5_000)
        }

        do {
            let response = try await MKLocalSearch(request: request).start()
            searchResults = response.mapItems
            if response.mapItems.isEmpty {
                errorMessage = "검색 결과가 없습니다."
            }
        } catch {
            errorMessage = "검색 실패: \(error.localizedDescription)"
        }
    }

    /// 선택한 목적지까지의 도보 경로를 조회한다.
    private func requestRoute(to item: MKMapItem) async {
        guard let origin = location.coordinate else {
            errorMessage = "현재 위치를 아직 받지 못했습니다."
            return
        }
        isLoading = true
        defer { isLoading = false }

        do {
            route = try await provider.route(from: origin,
                                             to: item.placemark.coordinate,
                                             destinationName: item.name ?? "")
            searchResults = []
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

#Preview {
    ContentView()
}
