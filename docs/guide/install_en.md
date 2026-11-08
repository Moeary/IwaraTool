# Installation and First Steps

[简体中文](./install.md) | [日本語](./install_ja.md) | [Back to README](../../readme.md)

This page covers choosing a download, installing on each platform, getting past the macOS first-launch security prompt, configuring the proxy, and the basic workflow.

## Choosing a download

Download the file for your system from [Releases](https://github.com/Moeary/IwaraTool/releases):

| System | File | Notes |
| --- | --- | --- |
| Windows 10 / 11 (64-bit) | `IwaraTool-Windows-AMD64-<version>.exe` | Single file, double-click to run |
| Linux (x86_64) | `IwaraTool-Linux-AMD64-<version>` | Built on `ubuntu-latest`; run from source on distributions with an older glibc |
| macOS · Apple Silicon (M-series chips) | `IwaraTool-macOS-AppleSilicon-ARM64-v<version>.dmg` | Recommended |
| macOS · Intel processors | `IwaraTool-macOS-Intel-x86_64-v<version>.dmg` | Recommended |

Each macOS build also ships a `.zip` with the same app for people who prefer extracting it. Every file comes with a `.sha256` checksum.

**Which chip does my Mac have?** Click  in the top-left corner → About This Mac. If **Chip** says **Apple M1 / M2 / M3 / M4…**, take AppleSilicon-ARM64; if it says "Processor … Intel Core …", take Intel-x86_64. Apple Silicon Macs can run the Intel build through Rosetta, only slower; Intel Macs cannot run the ARM64 build. CI verifies on macOS 15; older macOS versions are unverified.

## macOS: install from the DMG

1. Double-click the downloaded `.dmg` to open the installer window.
2. Drag the **IwaraTool** icon onto the **Applications** folder, following the arrow.
3. Eject the "IwaraTool" disk from the Finder sidebar; the `.dmg` can then be deleted.
4. Open IwaraTool from Applications or Launchpad. Do not run it from the DMG window: the image is read-only and cannot be replaced by an update.

To update, repeat these steps and choose Replace. Settings, history and subscriptions live in `~/Library/Application Support/IwaraTool/` and survive replacing the app.

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
- Neither is on: open Settings → Proxy, enable "Login / parsing proxy", enter the client's local HTTP / mixed port and click Apply.

| Proxy client | Usual default address |
| --- | --- |
| Clash Verge Rev (prefilled) | `http://127.0.0.1:7897` |
| Older Clash for Windows, ClashX and similar | `http://127.0.0.1:7890` |
| Anything else | The port shown in that client's settings; any port works |

The video download proxy only affects the video files themselves (and aria2's `all-proxy`); covers, avatars and metadata always follow the login / parsing proxy. Upgrading from an older version keeps your existing proxy settings. Linux does not read the desktop's system proxy: enable the proxy in the app or set `HTTPS_PROXY` and friends.

## Basic workflow

1. **Log in (optional)** under Settings → Account Login. Needed for private videos, "My subscriptions" on Home, and importing followed authors.
2. **Download**: paste an author, playlist, search or video URL on the Download Workbench, pick a download rule and submit. Unfinished tasks resume the next time you open the app.
3. **Search**: choose a source and scope on the Search page and enter keywords. Iwara's search API cannot restrict likes, duration and the like up front, so click **Filter** once results are in and set the conditions and a new order in the popup (see the [Usage Guide](./usage_en.md#post-search-filter)). Select results and click "Add selected" to download them.
4. **Subscribe** to authors or playlists from search results or an author page, then check the Subscriptions page for new works and download only what is new.

More in the [Usage Guide](./usage_en.md) and the [Reference](./reference_en.md).

## FAQ

**Home or Search never finishes loading**: the network most likely cannot reach Iwara directly. Make sure the proxy client is running and either turn on System Proxy / TUN or enter the right port in the app. Clash Verge Rev uses `7897` by default, not the `7890` found in older guides.

**macOS says the app "can't be opened" or "is damaged"**: see the first-launch section above. Opening the ARM64 build on an Intel Mac reports that it is not supported on this Mac; download the Intel-x86_64 build instead.

**Where is my data, and how do I uninstall?** On macOS settings and caches live in `~/Library/Application Support/IwaraTool/`, and videos go to `~/Downloads/IwaraTool/` by default. To uninstall, move IwaraTool from Applications to the Trash, and delete the data folder too if you no longer need it. The Windows and Linux builds keep their data in the `data/` folder next to the program. See [Local Data and Cache](./data_en.md).

**How do I update?** Settings → GitHub Release Updates checks for a new version; download it from Releases and replace the old program. Your data stays.
