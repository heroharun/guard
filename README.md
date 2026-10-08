# PC Guard (macOS)

Catch who touches your Mac while you are away. When the screen is dark, asleep,
or locked, PC Guard snaps a photo from the camera and posts it to Slack the
moment someone interacts with the machine. Built for working in public places
like cafés.

> It takes a photo of whoever triggers your own device, like a security camera.
> It does **not** log keystrokes or any typed content. It only reports *that*
> input happened, never *what* was typed.

## What it does

While the screen is **dark, asleep, or locked**:

| Trigger | Sent to Slack |
|---|---|
| Screen brightened from dark | message + photo |
| Woke from display sleep (≥3s dark) | message + photo |
| Unlocked | message + photo |
| Keyboard touched (dark/asleep) | message + photo |
| Mouse moved (dark/asleep) | message + photo |
| Keyboard/mouse while only locked (bright) | message only |

Every photo is also saved locally under `captures/`.

## How it works

A small background agent polls the system once a second and reacts to:

- **Display wake** from `pmset` log (`Display is turned on`).
- **Brightness** via the private `DisplayServicesGetBrightness` API (real 0.0–1.0
  value; the `ioreg` backlight node is frozen on recent Macs and is not used).
- **Lock state** and **input activity** via Quartz. Input is detected only as
  "seconds since last event," so no content is ever captured.

### Camera capture (the important detail)

macOS grants camera access to the *responsible process*. A Python service that
spawns a camera tool is denied ("Camera access not granted"), but a tool run
**directly** by `launchd` is allowed. So capture is delegated to a separate
LaunchAgent that runs the camera tool directly; the main service triggers it
with `launchctl kickstart` and uploads the resulting image. Capture takes about
2–3 seconds, exposed correctly (not black) thanks to a short camera warm-up.

## Requirements

- macOS (Apple Silicon tested)
- [`imagesnap`](https://github.com/rharder/imagesnap): `brew install imagesnap`
- Python 3 with PyObjC Quartz: `python3 -m pip install --user pyobjc-framework-Quartz`
- A Slack app with bot scopes `chat:write` and `files:write`, invited to your
  target channel (`/invite @your-bot`). An incoming webhook is optional (text
  fallback).

## Install

```bash
git clone <your-fork-url> guard && cd guard
brew install imagesnap
python3 -m pip install --user pyobjc-framework-Quartz

export SLACK_BOT_TOKEN="xoxb-..."       # bot token
export SLACK_CHANNEL="C0XXXXXXX"        # channel id the bot is in
export SLACK_WEBHOOK_URL="https://..."  # optional text fallback
./install.sh
```

The first capture asks for camera permission; allow it. Remove everything with
`./uninstall.sh`.

## Configuration

Set these as environment variables (in the generated LaunchAgent plist):

| Variable | Default | Meaning |
|---|---|---|
| `POLL_SECONDS` | `1` | Poll interval |
| `COOLDOWN` | `20` | Min seconds between repeats of the same keyboard/mouse event |
| `WAKE_COOLDOWN` | `10` | Min seconds between photos (dedupes overlapping triggers) |
| `SETTLE_SECONDS` | `0.2` | Pause before capture after a trigger |
| `DARK_LEVEL` | `0.15` | Brightness below this (0–1) counts as "dark" |
| `RISE_DELTA` | `0.15` | Sudden brightness rise that counts as "brightened" |
| `MIN_DARK_SECONDS` | `3` | Dark duration before a wake counts |
| `DEBUG_BRIGHT` | `1` | Log brightness changes (set `0` to quiet) |

## Privacy and intent

PC Guard is an anti-tamper tool for **your own device**. It never records what
is typed. Install it only on machines you own or are authorized to monitor.

## Files

- `pc_guard.py` — the watcher
- `com.pcguard.guard.plist.template` — main LaunchAgent (filled by `install.sh`)
- `com.pcguard.capture.plist.template` — direct camera LaunchAgent
- `install.sh` / `uninstall.sh`
- `captures/` — saved photos (git-ignored)

## License

MIT
