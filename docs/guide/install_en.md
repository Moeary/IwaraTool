# Installation and First Steps

[简体中文](./install.md) | [日本語](./install_ja.md) | [Back to README](../../readme.md)

This page covers choosing a download, installing on each platform, getting past the macOS first-launch security prompt, configuring the proxy, and the basic workflow.

## Choosing a download

Download the file for your system from [Releases](https://github.com/Moeary/IwaraTool/releases):

| System | File | Notes |
| --- | --- | --- |
| Windows 10 / 11 (64-bit) | `IwaraTool-Windows-AMD64-v<version>.zip` | Extract and run `IwaraTool.exe` |
| macOS · Apple Silicon (M-series chips) | `IwaraTool-macOS-AppleSilicon-ARM64-v<version>.zip` | Extract to get `IwaraTool.app` |
| Android | `IwaraTool-Android-arm64-v8a-v<version>.apk` (recommended) | Pick by processor, see below; allow "install unknown apps" when asked |

Linux and Intel Macs have no prebuilt package for now: run from source (see [Reference](./reference_en.md#run--build-from-source)) or keep using v1.2.3.

**Which chip does my Mac have?** Click  in the top-left corner → About This Mac. If **Chip** says **Apple M1 / M2 / M3 / M4…**, it is Apple Silicon. CI verifies on macOS 15; older macOS versions are unverified.

## macOS: install

1. Double-click the downloaded `.zip` to extract `IwaraTool.app`.
2. Drag `IwaraTool.app` into the Applications folder.
3. Open IwaraTool from Applications or Launchpad.

To update, replace the old `IwaraTool.app` with the new one. Settings, history and subscriptions live in `~/Library/Application Support/IwaraTool/` and survive replacing the app.

## Windows: install and update

Extract the archive into any folder and run `IwaraTool.exe`. Settings and history live in the `data/` folder beside the program: to update, extract the new version into the same folder. Coming from v1.2 or earlier, extract it into the folder of the old `.exe` as well (elsewhere, use Settings → Storage → Backup & Logs → Import from an older version…).

The Windows build is not code-signed: on first run SmartScreen may say "Windows protected your PC"; click More info → Run anyway. Some antivirus programs may also flag it by mistake; allow the program's folder if so. Releases are built from this repository's source by GitHub Actions, so you can check it or run from source.

## Android: install and update

Android comes in one smaller APK per processor type:

- `arm64-v8a`: nearly every phone and tablet of recent years, **the first choice**;
- `armeabi-v7a`: older 32-bit ARM devices;
- `x86_64`: emulators and some Chromebooks;
- `universal`: all of them in one larger file, for when you are not sure.

When the app finds a new version it downloads the APK for your device directly. Allow "install unknown apps" when installing the `.apk`. Every release carries the same signature, so a newer `.apk` installs over the old one as an update and keeps your data.

## macOS: the first-launch security prompt

IwaraTool has no paid Apple Developer ID signature and is not notarized by Apple, so macOS blocks it the first time. macOS does this for every non-notarized app; allow it once and it opens normally afterwards.

**macOS 15 (Sequoia) and later**

1. Open IwaraTool once and click **Done** in the "Apple could not verify…" dialog.
2. Open System Settings → Privacy & Security and scroll down to the Security section.
3. Next to "IwaraTool was blocked to protect your Mac", click **Open Anyway** and enter your login password.
4. Click **Open Anyway** again in the dialog that follows.

**macOS 14 (Sonoma) and earlier**: in Applications, Control-click (or right-click) IwaraTool → Open, then click Open in the dialog.

**"IwaraTool is damaged and can't be opened. You should move it to the Trash."** This is usually the quarantine flag the browser added to the download, not real damage. After making sure the file came from this project's Releases page, run in Terminal:

```bash
xattr -dr com.apple.quarantine /Applications/IwaraTool.app
```

Then open the app again.

## Proxy

Reaching Iwara usually needs a proxy. IwaraTool's **login / parsing proxy** and **video download proxy** are both **off by default**; while they are off, requests follow the system proxy or TUN:

- Your proxy client has **System Proxy** or **TUN mode** on: nothing to configure.
- Neither is on: open Settings → Account and network → Proxy, enable "Login / parsing proxy", enter the client's local HTTP / mixed port and click Apply.

| Proxy client | Usual default address |
| --- | --- |
| Clash Verge Rev (prefilled) | `http://127.0.0.1:7897` |
| Older Clash for Windows, ClashX and similar | `http://127.0.0.1:7890` |
| Anything else | The port shown in that client's settings; any port works |

The video download proxy only affects the video files themselves (and aria2's `all-proxy`); covers, avatars and metadata always follow the login / parsing proxy. Upgrading from an older version keeps your existing proxy settings. Linux does not read the desktop's system proxy: enable the proxy in the app or set `HTTPS_PROXY` and friends.

## Basic workflow

1. **Sign in (optional)** from the account entry at the foot of the sidebar (the avatar at the top right on phones) or in Settings → Account and network. Needed for private videos, "My subscriptions" on Home, and importing followed authors.
2. **Download**: paste an author, playlist, search or video URL on the Downloads page and click Download; the sheet that opens shows what will be queued and with which rule. Unfinished tasks resume the next time you open the app, and finished videos are listed and played in the Library.
3. **Search**: choose a source and scope on the Search page and enter keywords. Iwara's search API cannot restrict likes, duration and the like up front, so click **Filter** once results are in and set the conditions and a new order in the sheet (see the [Usage Guide](./usage_en.md#filtering-after-a-search)). Click a cover for its details and player; select several results and click "Download N" to download them together.
4. **Subscribe** to authors or playlists from search results or an author page, then check the Subscriptions page for new works and download only what is new.

More in the [Usage Guide](./usage_en.md) and the [Reference](./reference_en.md).

## FAQ

**Home or Search never finishes loading**: the network most likely cannot reach Iwara directly. Make sure the proxy client is running and either turn on System Proxy / TUN or enter the right port in the app. Clash Verge Rev uses `7897` by default, not the `7890` found in older guides.

**macOS says the app "can't be opened" or "is damaged"**: see the first-launch section above. Opening the ARM64 build on an Intel Mac reports that it is not supported on this Mac; download the Intel-x86_64 build instead.

**Where is my data, and how do I uninstall?** On macOS settings and caches live in `~/Library/Application Support/IwaraTool/`, and videos go to `~/Downloads/IwaraTool/` by default. To uninstall, move IwaraTool from Applications to the Trash, and delete the data folder too if you no longer need it. The Windows and Linux builds keep their data in the `data/` folder next to the program. See [Local Data and Cache](./data_en.md).

**How do I update?** Settings → About checks for a new version; download it from Releases and replace the old program. Your data stays (on Android, install the new APK over the old one).
