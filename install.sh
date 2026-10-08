#!/bin/zsh
# PC Guard installer: fills the plist templates with your local paths and
# Slack credentials, installs both LaunchAgents, and starts the guard.
set -e

GUARD_DIR="${0:A:h}"
PYTHON="$(command -v python3)"
IMAGESNAP="$(command -v imagesnap)"
PYTHONPATH_DIR="$($PYTHON -c 'import site;print(site.getusersitepackages())')"
LA="$HOME/Library/LaunchAgents"
UID_N="$(id -u)"

: "${SLACK_BOT_TOKEN:?set SLACK_BOT_TOKEN (xoxb-...) before running}"
: "${SLACK_CHANNEL:?set SLACK_CHANNEL (e.g. C0XXXX) before running}"
SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"

[ -n "$IMAGESNAP" ] || { echo "imagesnap missing: brew install imagesnap"; exit 1; }
$PYTHON -c 'import Quartz' 2>/dev/null || python3 -m pip install --user pyobjc-framework-Quartz

mkdir -p "$LA" "$GUARD_DIR/captures"

fill() { sed -e "s#__PYTHON__#$PYTHON#g" -e "s#__IMAGESNAP__#$IMAGESNAP#g" \
             -e "s#__GUARD_DIR__#$GUARD_DIR#g" -e "s#__PYTHONPATH__#$PYTHONPATH_DIR#g" \
             -e "s#__SLACK_BOT_TOKEN__#$SLACK_BOT_TOKEN#g" -e "s#__SLACK_CHANNEL__#$SLACK_CHANNEL#g" \
             -e "s#__SLACK_WEBHOOK_URL__#$SLACK_WEBHOOK_URL#g" "$1"; }

fill com.pcguard.capture.plist.template > "$LA/com.pcguard.capture.plist"
fill com.pcguard.guard.plist.template   > "$LA/com.pcguard.guard.plist"

launchctl bootout   "gui/$UID_N/com.pcguard.capture" 2>/dev/null || true
launchctl bootout   "gui/$UID_N/com.pcguard.guard"   2>/dev/null || true
launchctl bootstrap "gui/$UID_N" "$LA/com.pcguard.capture.plist"
launchctl bootstrap "gui/$UID_N" "$LA/com.pcguard.guard.plist"

echo "Installed. First capture will ask for camera permission; allow it."
echo "Logs: tail -f $GUARD_DIR/pc_guard.log"
