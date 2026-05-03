# launchd agents

macOS launchd plists for Friday's background processes. These are templates — install
them by symlinking into `~/Library/LaunchAgents/` and loading with `launchctl bootstrap`.

All plists assume:
- Friday lives at `/Users/kenburleson/Projects/Friday`
- `uv` is at `/Users/kenburleson/.local/bin/uv`

If those paths differ on a future machine, edit the plist before loading.

## Currently defined

| Plist | Purpose | Phase | Cadence |
|---|---|---|---|
| `com.friday.scribe.plist` | Inbox watcher | 1 | KeepAlive (continuous) |
| `com.friday.poller.calendar.plist` | Google Calendar poller | 4 | every 60 min |
| `com.friday.poller.drive.plist` | Google Drive poller | 4 | every 30 min |
| `com.friday.poller.slack.plist` | Slack poller | 4 | every 15 min |
| `com.friday.promoter.plist` | Engagement-driven promoter | 5 | every 5 min |

## Install

Each plist installs the same way — symlink into `~/Library/LaunchAgents/`,
then bootstrap:

```sh
LAUNCH_AGENTS=~/Library/LaunchAgents
PLIST_DIR=/Users/kenburleson/Projects/Friday/scripts/launchd

for plist in com.friday.scribe.plist \
             com.friday.poller.calendar.plist \
             com.friday.poller.drive.plist \
             com.friday.poller.slack.plist \
             com.friday.promoter.plist; do
    ln -sfn "$PLIST_DIR/$plist" "$LAUNCH_AGENTS/$plist"
    launchctl bootstrap "gui/$(id -u)" "$LAUNCH_AGENTS/$plist"
done
```

Or install just one:

```sh
ln -sfn /Users/kenburleson/Projects/Friday/scripts/launchd/com.friday.poller.calendar.plist \
    ~/Library/LaunchAgents/com.friday.poller.calendar.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.friday.poller.calendar.plist
```

**Pollers require OAuth setup first.** Run `python -m auth.google_calendar`,
`python -m auth.google_drive`, `python -m auth.slack` before bootstrapping
the corresponding agent — otherwise the poller will exit with a missing-secret
error every cycle.

## Status

```sh
launchctl print gui/$(id -u)/com.friday.scribe
tail -f /Users/kenburleson/Projects/Friday/.logs/scribe.watcher.stderr.log
```

## Uninstall

```sh
launchctl bootout gui/$(id -u)/com.friday.scribe
rm ~/Library/LaunchAgents/com.friday.scribe.plist
```

## Notes

- `KeepAlive=true` + `ThrottleInterval=10` (Scribe only) means launchd will restart the
  watcher within 10s of any crash — handy because watchdog observers occasionally die
  under heavy filesystem churn.
- Pollers use `StartInterval` instead of `KeepAlive`: launchd runs them on a fixed
  cadence and lets each `--once` invocation exit cleanly. If an invocation crashes,
  launchd just retries on the next interval rather than busy-looping.
- `RunAtLoad=true` means the watcher and pollers all run once at login, then begin their
  schedule.
- Logs go to `.logs/{component}.{stdout,stderr}.log`. The Python code itself writes
  structured JSONL to `.logs/{scribe.*,poller.*}.jsonl`.
