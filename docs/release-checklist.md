# Release checklist

Before tagging a release.

## Diagnostics

The temporary diagnostics are Debug page switches, off by default (`DIAGNOSTICS` in `launcher/lbppc_core.py`):
the slow-frame profiler (`LBPPC_PROFILE`), optimization switches (`LBPPC_TOGGLES`, which sets `BB_TOGGLE_FILE` in
`run.py`) and the friends menu log (`LBPPC_FRIENDS_LOG`, `src/probe.c`). Check they are still off by default.

Keep on:

- The "Sync:" lines (`src/probe.c`). `docs/troubleshooting.md` tells players to send them when the game diverges.
- `BB_FRAME_STATS`, the 5-second performance lines `docs/troubleshooting.md` explains.
- The "Clear:" lines (`docs/rendering-fixes.md`).

## Tests

- [ ] A clean folder: unzip the release zip somewhere new, first start, prepare, play to the Pod.
- [ ] Quit Game from the pause menu, twice, including right after a save.
- [ ] Online co-op at 16:9 and with one player at ultrawide; a level start right after leaving another.
- [ ] The in-game friends list with a friend online.
- [ ] Make a report: the zip has the logs and no password or user name.

## Publish

- [ ] `VERSION` in `launcher/lbppc_core.py` and the status line in `README.md`.
- [ ] Release notes in `docs/releases/<version>.md`.
- [ ] `bash packaging/windows/package.sh`, then the GitHub release with the zip and the notes.
- [ ] The website: download link and version (`lbppc-site`), and deploy.
- [ ] Announce on the Discord.
