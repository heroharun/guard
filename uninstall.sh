#!/bin/zsh
UID_N="$(id -u)"; LA="$HOME/Library/LaunchAgents"
launchctl bootout "gui/$UID_N/com.pcguard.guard"   2>/dev/null || true
launchctl bootout "gui/$UID_N/com.pcguard.capture" 2>/dev/null || true
rm -f "$LA/com.pcguard.guard.plist" "$LA/com.pcguard.capture.plist"
echo "PC Guard removed."
