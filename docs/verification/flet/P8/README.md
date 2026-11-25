# P8 Android — verification (code and packaging; real devices pending)

Screenshots come from a desktop run told to behave as Android
(`flet_viewports.py --demo --platform android`), so they show what the phone
build hides, not real-device rendering:

| File | What it shows |
| --- | --- |
| `home_360x800.png`, `home_411x960.png` | Bottom bar without Repair / Tools |
| `settings_360x800.png` | Categories without Tools and Shortcuts |
| `settings_downloads_360x800.png` | No "Open download folder", no completed-card click setting |
| `settings_system_360x800.png` | Start-up card without window notes; no custom player command |
| `downloads_dark_360x800.png`, `library_360x800.png` | Task rows and library on a phone |
| `tablet_settings_1280x800.png` | Android tablet: rail without Repair / Tools, settings without Tools / Shortcuts |

`--route /repair` with `--platform android` opens Home (desktop-only routes are refused).

Checked by unit tests (`scripts/tests/flet_ui/test_flet_platform.py`,
`scripts/tests/core/test_clipboard_links.py`): capabilities, route guard,
folder actions, back key (steps back, goes Home, then leaves), pause saving,
download folder move, copied-link parsing.

APK: built by the `build-flet-android` job of `.github/workflows/build.yml`
(on demand: `gh workflow run build.yml --ref feat/flet-ui -f target=flet`);
`build_flet.py` audits the APK and the app archive inside it for Qt / `app/ui`.

**Pending on real devices** (plan 9, P8): an ordinary phone, a 21:9 phone, a
foldable (inner and outer screens) and a tablet, each doing browse → download →
play once; plus the back gesture, the cleartext loopback stream, the external
download folder, the wakelock and the copied-link prompt.
