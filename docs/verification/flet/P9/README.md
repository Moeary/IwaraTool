# P9 PySide6 vs Flet — comparison and decision

Machine: Apple M5, 16 GB, macOS 26.6. Both interfaces from source (pixi environments), offline
against `demo_fixture` (made-up Iwara), 1280 × 800, three runs each, medians below.
Reproduce with:

    pixi run -e flet python scripts/tools/compare_frontends.py --out tmp/compare --runs 3 --records 10000

Raw numbers: `raw.json`.

## Measurements

| Measure | PySide6 | Flet |
| --- | ---: | ---: |
| Launch → first frame (s) | 0.87 | 0.48 |
| Memory at first frame (MB) | 334 | 278 |
| Memory 8 s later, home feeds and covers loaded (MB) | 377 | 318 |
| Open the library with 10 000 records (s) | 0.06 | 0.26 |
| Memory with that library open (MB) | 491 | 339 |

How to read them:

- "First frame" is launch (interpreter start and imports included) until the window is drawn:
  `QWidget.grab()` for PySide6, `page.take_screenshot()` (answered by the client after drawing)
  for Flet.  PySide6 builds all nine pages before showing the window; Flet builds only the
  current one, so part of the difference is design, not toolkit.  The Flet client was already
  unpacked (`~/.flet/client`); a first launch after installing also unpacks it.
- Flet memory is the Python process plus its Flutter client (a separate process, started by
  `open -n` on macOS and found by its command line).  macOS RSS counts shared framework pages
  for both, so the numbers compare like with like but are not "private memory".
- Library: PySide6 fills a table model synchronously; Flet reads the records and their file
  states in a thread and then sends the first page of rows to the client.  0.26 s for
  10 000 records is well under the point where it feels slow; filtering and sorting run on the
  in-memory list in both.
- Not measured: scrolling smoothness and three concurrent downloads (flet_migration.md item 2).
  The script has no frame-time probe for either toolkit, and the demo fixture has no real
  transfers.  Both remain to check by hand before replacing the old interface.

## Package size

| Artifact | PySide6 (v1.2.3 release) | Flet (CI, this branch) |
| --- | ---: | ---: |
| Windows | 32 MB (single exe) | 54 MB (zipped CI artifact) |
| macOS Apple Silicon | 61 MB zip / 69 MB dmg | 68 MB (zipped CI artifact) |
| Android | — | 184 MB universal APK (83 MB zipped) |

The universal APK carries the native libraries of every ABI; releases should set
`split_per_abi = true` in `[tool.flet.android]` and publish one APK per ABI.

## Feature parity (from the plan's section 10 trade-off table)

Everything in the old interface has a Flet counterpart or a recorded replacement, except:

| Missing in Flet | Impact |
| --- | --- |
| Tray, close to tray, launch at login | High for people who leave downloads running; Flet asks on close instead |
| System notifications (subscription refresh, finished downloads) | Medium; in-app notices only |
| Dropping files / folders onto repair and tools | Low; Browse and typed paths work |
| Mouse side buttons, multi-chord shortcuts | Low; Esc / Alt+← work |
| Per-row multi-select on the downloads page | Low; bulk actions are in the page menu |
| Choosing a font from a list of installed fonts | Low; a name field with fallback |

New in Flet: phone and tablet layouts, an Android build, the repair step flow, the reorganised
library, settings that save immediately.

## Decision

**Do not replace the PySide6 interface in v1.3.0 yet.**  Ship Flet next to it:

1. v1.3.0 keeps the PySide6 desktop builds as the main download and adds the Flet builds
   (`IwaraTool-Flet-*`) as a preview, plus the Android APK, which only Flet can provide.
2. Flet replaces PySide6 on the desktop once (a) tray / close to tray and launch at login and
   (b) system notifications exist for the Flet build, and (c) Android passes the P8 real-device
   checks.  None of these needs core changes; they are platform integration (a Flet extension or a
   small native helper) and packaging work.
3. Performance is not a blocker so far: Flet starts faster and uses less memory here; its only
   slower number (library opening, 0.26 s) is acceptable.  Scrolling and concurrent downloads
   still need a hand check (above).

Until then the hard rules of AGENTS.md stay: the old interface remains buildable and unchanged,
and shared logic lives in `app/core` and `app/presentation`.
