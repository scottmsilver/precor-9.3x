#!/usr/bin/env bash
# Asserts the avahi service row is accepted by the manifest parser and the
# service XML is well-formed.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./lib-artifacts.sh

manifest_rows ./manifest.txt | grep -q 'treadmill\.avahi-service' \
  || { echo "FAIL: avahi row rejected/absent in manifest_rows output"; exit 1; }

# XML well-formed (xmllint if present, else python).
if command -v xmllint >/dev/null 2>&1; then
  xmllint --noout treadmill.avahi-service
else
  python3 -c 'import xml.dom.minidom,sys; xml.dom.minidom.parse("treadmill.avahi-service")'
fi
echo "PASS: manifest accepts avahi row; service XML well-formed"
