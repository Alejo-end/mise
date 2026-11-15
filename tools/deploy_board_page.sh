#!/bin/bash
# Deploy the board page (static assets only: no app restart) to both board apps, stamped with the commit and time,
# with a per-app build.json the page compares against. Run from valio-challenge/ on a committed tree.
# Board password: SSH key, SSH_ASKPASS or typed when asked. Usage: tools/deploy_board_page.sh [board-ip]
set -euo pipefail
BOARD=${1:-172.20.10.2}
BUILD="$(git rev-parse --short HEAD)-$(date +%H%M)"
WHEN="$(date '+%Y-%m-%d %H:%M')"
TMP=$(mktemp -d)
sed "s/__PAGE_BUILD__/$BUILD/g" device/assets/index.html > "$TMP/index.html"
for app in safe-kitchen-agent safe-kitchen-test; do
  printf '{"app": "%s", "build": "%s", "deployed_at": "%s"}\n' "$app" "$BUILD" "$WHEN" > "$TMP/build-$app.json"
  scp -q -o ConnectTimeout=15 "$TMP/index.html" "arduino@$BOARD:/home/arduino/ArduinoApps/$app/assets/index.html"
  scp -q -o ConnectTimeout=15 "$TMP/build-$app.json" "arduino@$BOARD:/home/arduino/ArduinoApps/$app/assets/build.json"
  # the shared product set (products/active.json): names, enrolment choices and ordering units on the page
  scp -q -o ConnectTimeout=15 products/active.json "arduino@$BOARD:/home/arduino/ArduinoApps/$app/assets/products.json"
done
echo "deployed page build $BUILD to both apps on $BOARD"
