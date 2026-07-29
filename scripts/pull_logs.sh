#!/usr/bin/env bash
# Orange Pi 5(raseyes)의 운영 CSV 로그와 경고 이벤트 클립을 PC로 수집한다.
# tailscale 경유라 집/실외 어느 네트워크에서든 동작한다. PC에서 실행할 것.

set -euo pipefail

REMOTE="raseyes"
REMOTE_LOGS="~/RasEyes/logs"
DEST="logs_archive"

mkdir -p "$DEST/events"

# Pi의 CsvLogger가 세션마다 별도 파일(raseyes_log_<타임스탬프>.csv)에 기록하므로
# 원격 파일명을 그대로 받으면 된다. --delete를 쓰지 않아 PC 아카이브는 Pi와 독립적으로 누적된다.
echo "▶ CSV 로그 수집 중..."
rsync -av "$REMOTE:$REMOTE_LOGS/raseyes_log_*.csv" "$DEST/" || \
    echo "  (CSV 없음 — 서비스가 아직 실행되지 않았거나 미배포 상태)"

# 클립은 --delete 없이 증분 미러 — PC는 아카이브이므로 Pi의 rotation과 독립적으로 누적한다.
echo "▶ 경고 이벤트 클립 수집 중..."
if ssh "$REMOTE" "test -d $REMOTE_LOGS/events"; then
    rsync -av "$REMOTE:$REMOTE_LOGS/events/" "$DEST/events/"
else
    echo "  (클립 없음 — 아직 HIGH 경보가 저장되지 않았거나 미배포 상태)"
fi

echo "✓ 완료: $DEST (분석: python scripts/analyze_logs.py $DEST/*.csv)"
