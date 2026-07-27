#!/usr/bin/env bash
# Orange Pi 5(raseyes)의 운영 CSV 로그와 경고 이벤트 클립을 PC로 수집한다.
# tailscale 경유라 집/실외 어느 네트워크에서든 동작한다. PC에서 실행할 것.

set -euo pipefail

REMOTE="raseyes"
REMOTE_LOGS="~/RasEyes/logs"
DEST="logs_archive"

mkdir -p "$DEST/events"

# Pi의 CsvLogger는 프로세스 시작마다 CSV를 새로 쓴다(mode="w").
# 같은 파일명으로 받으면 이전 세션 로그가 덮여 사라지므로,
# 원격 파일의 mtime을 파일명에 박아 세션별로 보존한다 (재실행 시 멱등).
echo "▶ CSV 로그 수집 중..."
STAMP="$(ssh "$REMOTE" "date -r $REMOTE_LOGS/raseyes_log.csv +%Y%m%d_%H%M%S")"
rsync -av "$REMOTE:$REMOTE_LOGS/raseyes_log.csv" "$DEST/raseyes_log_$STAMP.csv"

# 클립은 --delete 없이 증분 미러 — PC는 아카이브이므로 Pi의 rotation과 독립적으로 누적한다.
echo "▶ 경고 이벤트 클립 수집 중..."
if ssh "$REMOTE" "test -d $REMOTE_LOGS/events"; then
    rsync -av "$REMOTE:$REMOTE_LOGS/events/" "$DEST/events/"
else
    echo "  (클립 없음 — 아직 HIGH 경보가 저장되지 않았거나 미배포 상태)"
fi

echo "✓ 완료: $DEST (분석: python scripts/analyze_logs.py $DEST/*.csv)"
