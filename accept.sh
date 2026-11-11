#!/bin/sh
# One-click local acceptance for the event recorder.
#
# Runs entirely against local synthetic media and the bundled ffmpeg:
#   * normal regression (original CLI and decode path)
#   * out-of-order arrival and redelivery determinism
#   * truncated fragment followed by a complete redelivery
#   * permanent fragment loss and independently-decodable recovery
#   * capture restart with timestamp reset and init-section switch
#   * same-sequence/different-content conflict rejection
#   * forced process kills at both publication crash points, followed by
#     replay and byte-for-byte comparison with an uninterrupted baseline
#
# Usage:  sh accept.sh
set -e

cd "$(dirname "$0")"
export FFMPEG="$PWD/bin/ffmpeg"
export PYTHONPATH="$PWD"

echo "==> Using ffmpeg: $FFMPEG"
"$FFMPEG" -version | head -1

# Fresh fixture and output cache so the run is reproducible.
rm -rf tests/_fixture_cache

echo
echo "==> Running unittest suite (synthesizes fixtures and decodes output)"
python3 -m unittest discover -s tests -v

echo
echo "==> All acceptance checks passed."
