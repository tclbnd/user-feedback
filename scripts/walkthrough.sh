#!/bin/sh
# Four-point crash walkthrough against a no-failure reference sink.
set -e
cd "$(dirname "$0")/.."
mkdir -p demo-run
BATCH=scripts/batch.json
REF=demo-run/reference

rm -rf demo-run/c-* demo-run/u1 demo-run/wm "$REF"
python3 outbox.py "$REF" lease p 1 >/dev/null
python3 outbox.py "$REF" lease q 1 >/dev/null
python3 outbox.py "$REF" apply "$BATCH" --lease 1 >demo-run/ref-view.json

for point in after-outbox after-sink after-checkpoint after-ack; do
  root="demo-run/c-$point"
  echo "=== crash at $point ==="
  python3 outbox.py "$root" lease p 1 >/dev/null
  python3 outbox.py "$root" lease q 1 >/dev/null
  set +e
  python3 outbox.py "$root" apply "$BATCH" --lease 1 --crash "$point" 2>demo-run/err.txt
  code=$?
  set -e
  echo "crash exit=$code"
  cat demo-run/err.txt
  python3 scripts/summarize.py "$root" "mid "
  python3 outbox.py "$root" apply "$BATCH" --lease 1 >/dev/null
  echo "recovery exit=$?"
  python3 outbox.py "$root" apply "$BATCH" --lease 1 >demo-run/third.json
  echo "third run exit=$? (must be 0, no duplicate commits)"
  python3 scripts/compare.py "$root"
done

echo "=== unknown commit state: after-sink crash, then after-outbox crash ==="
python3 outbox.py demo-run/u1 lease p 1 >/dev/null
python3 outbox.py demo-run/u1 lease q 1 >/dev/null
set +e
python3 outbox.py demo-run/u1 apply "$BATCH" --lease 1 --crash after-sink 2>/dev/null
echo "sink crash exit=$?"
python3 outbox.py demo-run/u1 apply "$BATCH" --lease 1 --crash after-outbox 2>/dev/null
echo "outbox crash exit=$?"
set -e
python3 scripts/summarize.py demo-run/u1 "unknown "
python3 outbox.py demo-run/u1 apply "$BATCH" --lease 1 >/dev/null
python3 scripts/summarize.py demo-run/u1 "reconciled "
python3 scripts/compare.py demo-run/u1

echo "=== lease hand-off: stale generation is refused with exit 3 ==="
python3 outbox.py demo-run/u1 lease p 2 >/dev/null
set +e
python3 outbox.py demo-run/u1 apply "$BATCH" --lease 1 2>demo-run/stale.txt
echo "stale apply exit=$?"
set -e
cat demo-run/stale.txt
python3 scripts/summarize.py demo-run/u1 "after-stale "
set +e
python3 outbox.py demo-run/u1 lease p 1 2>demo-run/stale2.txt
echo "stale lease exit=$?"
set -e
cat demo-run/stale2.txt

echo "=== watermark backpressure (watermark=1) ==="
python3 outbox.py demo-run/wm lease p 1 >/dev/null
python3 outbox.py demo-run/wm lease q 1 >/dev/null
python3 outbox.py demo-run/wm apply "$BATCH" --lease 1 --watermark 1 >demo-run/wm.json
python3 scripts/watermark_view.py

echo "=== replay deferred rows after raising the watermark ==="
python3 outbox.py demo-run/wm apply "$BATCH" --lease 1 --watermark 10 >demo-run/wm2.json
python3 scripts/watermark_replay.py

