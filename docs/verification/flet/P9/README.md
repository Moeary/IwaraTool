# P9 PySide6 vs Flet — comparison and decision

Machine: Apple M5, 16 GB, macOS 26.6. Both interfaces from source (pixi environments), offline
against `demo_fixture` (made-up Iwara), 1280 × 800, three runs each, medians below.
Reproduce with:

    pixi run -e flet python scripts/tools/compare_frontends.py --out tmp/compare --runs 3 --records 10000

Raw numbers: `raw.json`.

## Measurements

| Measure | PySide6 | Flet |
| --- | ---: | ---: |
| Launch → first frame (s) | 0.86 | 0.48 |
| Memory at first frame (MB) | 334 | 248 |
| Memory 8 s later, home feeds and covers loaded (MB) | 374 | 314 |
| Open the library with 10 000 records (s) | 0.06 | 0.25 |
| Memory with that library open (MB) | 491 | 491 |
| Library scroll step to drawn, median / worst (ms) | 27 / 37 | 139 / 260 |
| Library cover view: switch to drawn (s) | 0.15 | 0.77 |
| Cover view scroll step to drawn, median / worst (ms) | 6 / 7 | 190 / 499 |
| Memory after the cover view (MB) | 517 | 629 |
| 3 concurrent downloads (48 MB each): total (s) | 11.9 | 12.0 |
| …UI thread late on a 20 ms tick, p95 / worst (ms) | 5.5 / 48 | 2.4 / 8.3 |
| …completion events reaching the UI (of 3) | 3 | 3 |
| …memory while downloading (MB) | 388 | 314 |
| Python tracebacks in all runs | 0 | 0 |

How to read them:

- "First frame" is launch (interpreter start and imports included) until the window is drawn:
  `QWidget.grab()` for PySide6, `page.take_screenshot()` (answered by the client after drawing)
  for Flet.  PySide6 builds all nine pages before showing the window; Flet builds only the
  current one, so part of the difference is design, not toolkit.  The Flet client was already
  unpacked (`~/.flet/client`); a first launch after installing also unpacks it.
- Flet memory is the Python process plus its Flutter client (a separate process, started by
  `open -n` on macOS and found by its command line).  macOS RSS counts shared framework pages
  for both, so the numbers compare like with like but are not "private memory".
- Scroll steps are not the same unit.  PySide6 scrolls one page of an already built table or
  card list (it builds all 10 000 items up front: the slower "switch" number).  In Flet the
  Flutter client scrolls by itself without Python; Python is involved only when the end is
  near and the next 200 rows or cards are built, which is what the Flet step measures (a
  screenshot round trip alone takes 35 ms of it).  So Flet scrolls natively and pauses for
  0.1–0.2 s (cover view up to 0.5 s) every 200 items; PySide6 pays once when the view opens.
- Downloads: the same core in both, so the same total.  Flet's event loop stays far more
  responsive while three downloads report progress (PySide6's GUI thread carries the
  progress repaints); every completion event reached the UI in both.

### Fixed during P9

The first measurement exposed that every "load more" rebuilt every row or card shown so far:
the library list took 0.15 → 0.9 s per step by 4 200 rows, and the cover view 2.6 → 6.4 s per
step with memory above 1.1 GB.  Rows (`pages/library/page.py`) and cards (`VideoGrid`) are now
memoized components (`ft.memo`) with stable callbacks, so a step builds only the new ones
(numbers above).  Flet 1.1.3 loses the context of a component re-rendering inside a skipped
memoized parent; `use_shell()` falls back to the window's registered shell
(`stores/app_state.py`).

## Package size

| Artifact | PySide6 (v1.2.3 release) | Flet (CI, this branch) |
| --- | ---: | ---: |
| Windows | 32 MB (single exe) | 54 MB (zipped CI artifact) |
| macOS Apple Silicon | 61 MB zip / 69 MB dmg | 68 MB (zipped CI artifact) |
| Android | — | 184 MB universal APK (83 MB zipped) |

The universal APK carries the native libraries of every ABI; releases should set
`split_per_abi = true` in `[tool.flet.android]` and publish one APK per ABI.

## Feature parity (from the plan's section 10 trade-off table)

Everything in the old interface has a Flet counterpart or a recorded replacement.  Added after
the first P9 pass:

| Former gap | Now |
| --- | --- |
| Tray, close to tray | Windows: tray icon (`platform/tray_windows.py`, ctypes; CI adds and removes it on Windows); macOS: closing minimizes to the Dock; both behind "keep running when the window is closed" |
| Launch at login | `core/login_items.py`: Windows Run value `IwaraTool-Flet`, macOS LaunchAgent, Linux XDG autostart (separate from the classic build's entry) |
| System notifications | `core/system_notify.py`: macOS `osascript`, Windows toast, Linux `notify-send`; finished / failed downloads batched; in-app while the window has the focus |

Still different, all low impact: no drag and drop of files onto repair and tools, no mouse
side buttons or multi-chord shortcuts, no per-row multi-select on the downloads page, a font
name field instead of a list, no tray on Linux.

New in Flet: phone and tablet layouts, an Android build, the repair step flow, the reorganised
library, settings that save immediately.

## Decision

**Desktop: Flet can replace the PySide6 interface.**  It starts faster, uses less memory in normal
use, stays more responsive during downloads, and has no missing feature that matters.  Its one
weaker area is very large libraries: a 0.1–0.5 s pause each time the next 200 items are built,
against PySide6's single longer wait when opening the view.

Before the switch is made in a release (the maintainer's call, since it changes what users download):

1. Packaged desktop builds: every `build_flet.py` run now checks, inside the packaged app
   (`app/ui_flet/desktop_check.py`), that a close request with "keep running" on hides the
   window to the tray (Windows) or minimizes it to the Dock (macOS) and that it comes back,
   that the Windows tray icon runs, that the launch-at-login entry for the packaged
   executable is written, read back and removed, and that a system notification is accepted.
   Passed: Windows on the CI runner (hidden to the tray and shown again), macOS packaged on
   the M5 (minimized to the Dock and restored).  The CI Mac's client reports no window events,
   so there the window part is marked inconclusive (login item and notification pass).
   Still by hand: clicking the real close button and tray menu, and a real log-out / log-in.
2. Android: the APK starts on phone, foldable and tablet emulators in CI (`android-emulator`
   job; its first run caught a start-up crash, since fixed) and the back key leaves the app
   from the home page; the P8 real-device round (browse → download → play on each device class) is still open.

The plan for the release: publish the Flet builds as `IwaraTool-*` (main) and keep the PySide6
builds one more release as `IwaraTool-Classic-*`, then remove `app/ui/` and this file's hard rules.
