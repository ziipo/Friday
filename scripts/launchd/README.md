# launchd agents

macOS launchd plists for Friday's background processes. These are templates — install
them by symlinking into `~/Library/LaunchAgents/` and loading with `launchctl bootstrap`.

All plists assume:
- Friday lives at `/Users/kenburleson/Projects/Friday`
- `uv` is at `/Users/kenburleson/.local/bin/uv`

If those paths differ on a future machine, edit the plist before loading.

## Currently defined

| Plist | Purpose | Phase |
|---|---|---|
| `com.friday.scribe.plist` | Inbox watcher (Phase 1) | 1 |

## Install

```sh
ln -sfn /Users/kenburleson/Projects/Friday/scripts/launchd/com.friday.scribe.plist \
    ~/Library/LaunchAgents/com.friday.scribe.plist

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.friday.scribe.plist
```

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

- `KeepAlive=true` + `ThrottleInterval=10` means launchd will restart the watcher within
  10s of any crash — handy because watchdog observers occasionally die under heavy
  filesystem churn.
- `RunAtLoad=true` means the watcher starts at login.
- Logs go to `.logs/scribe.watcher.{stdout,stderr}.log`. The Python code itself writes
  structured JSONL to `.logs/scribe.{watcher,web,markdown,pdf,email}.jsonl`.
