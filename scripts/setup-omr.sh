#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -m)" != x86_64 ]] || [[ ! -f /etc/os-release ]]; then
  echo "This installer supports Ubuntu 24.04 x86_64. Install Audiveris 5.11 manually and set AUDIVERIS_BIN on other systems." >&2
  exit 1
fi
source /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 24.04 ]]; then
  echo "Use Ubuntu 24.04 x86_64 (or Docker), or install Audiveris manually and set AUDIVERIS_BIN." >&2
  exit 1
fi
mkdir -p .tools
package="$(mktemp .tools/audiveris-XXXXXX.deb)"
model="$(mktemp .tools/english-XXXXXX.traineddata)"
trap 'rm -f "$package" "$model"' EXIT
curl --fail --location --retry 3 \
  https://github.com/Audiveris/audiveris/releases/download/5.11.0/Audiveris-5.11.0-ubuntu24.04-x86_64.deb \
  --output "$package"
echo "f20113aaa33b3149ec8d6a09b2a7963360e65fafd92d69389987a85bbc3ec7a3  $package" | sha256sum --check
dpkg-deb --extract "$package" .tools/audiveris
mkdir -p .tools/tessdata
curl --fail --location --retry 3 \
  https://raw.githubusercontent.com/tesseract-ocr/tessdata/ced78752cc61322fb554c280d13360b35b8684e4/eng.traineddata \
  --output "$model"
echo "daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047  $model" | sha256sum --check
mv "$model" .tools/tessdata/eng.traineddata
chmod 644 .tools/tessdata/eng.traineddata
JAVA_TOOL_OPTIONS="-Djava.awt.headless=true -Dsun.java2d.uiScale=1" \
  .tools/audiveris/opt/audiveris/bin/Audiveris -help
