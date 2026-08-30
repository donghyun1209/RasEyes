"""네비게이션 코드 파서.

폰(iOS)이 BLE로 보낸 압축 코드(`R|50`)를 Pi가 발화할 영어 문장으로 바꾼다.
앱은 TMAP의 한국어 원문을 보내지 않는다 — Piper TTS가 영어 모델이므로
앱이 `ManeuverCode`로 정규화하고 문장 조립은 이쪽 책임이다.
"""

_TURN_MAPPING = {
    'F': 'Go straight',
    'L': 'Turn left',
    'R': 'Turn right',
    'U': 'Make a U-turn',
    'HL': 'Hard left',
    'SL': 'Slight left',
    'SR': 'Slight right',
    'HR': 'Hard right',
    'OP': 'Use the overpass',
    'UP': 'Use the underpass',
    'ST': 'Take the stairs',
    'RP': 'Take the ramp',
    'SP': 'Take the stairs or ramp',
    'W': 'Waypoint',
    'S': 'Start',
    'A': 'Arrive at destination',
    'X': 'Cross the crosswalk',
    'XL': 'Crosswalk on the left',
    'XR': 'Crosswalk on the right',
    'X8': "Crosswalk at 8 o'clock",
    'X10': "Crosswalk at 10 o'clock",
    'X2': "Crosswalk at 2 o'clock",
    'X4': "Crosswalk at 4 o'clock",
    'EV': 'Take the elevator',
}

# 표에 없는 코드를 만났을 때 쓰는 문구.
#
# ⚠ 여기에 'Proceed'나 'Go straight' 같은 기본값을 절대 두지 않는다. 앱은 공식
# 코드표에 없는 turnType을 `?`(ManeuverCode.unknown)로 남겨 그대로 보내는데,
# 그걸 직진으로 뭉개면 **회전을 놓치고도 조용히 지나간다** — 2.2 로드맵 Phase 1이
# 명시적으로 금지한 패턴이다. 모르면 모른다고 말해서 사용자가 직접 판단하게 한다.
_UNKNOWN_ACTION = 'Caution, unknown instruction'


def parse_nav_instruction(code: str) -> str:
    """BLE 압축 코드를 영어 TTS 발화 문장으로 변환한다.

    Args:
        code: 앱이 보낸 압축 코드. `<동작>|<거리m>` 형식이며 거리 파트는
            없을 수도 있다 (예: 'R|50', 'A').

    Returns:
        발화할 영어 문장 (예: 'Turn right in 50 meters').
        빈 코드면 빈 문자열.
    """
    if not code:
        return ""

    parts = code.split('|')
    action = _TURN_MAPPING.get(parts[0], _UNKNOWN_ACTION)

    # 거리는 BLE로 들어온 외부 입력이라 형식이 깨질 수 있다. 숫자가 아니면
    # 거리를 빼고 동작만 발화한다 — "in abc meters"를 말하는 것보다 낫다.
    distance = parts[1] if len(parts) > 1 else ""
    if distance.isdigit() and int(distance) > 0:
        return f"{action} in {distance} meters"
    return action
