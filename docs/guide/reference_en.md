# Reference: Platforms, URLs, Internals and Build

[简体中文](./reference.md) | [日本語](./reference_ja.md) | [Back to README](../../readme.md)

Details that most users never need. The README keeps only what helps you get started.

## Platform notes

- **Windows**: download `IwaraTool-Windows-AMD64-<version>.zip` from [Releases](https://github.com/Moeary/IwaraTool/releases), extract it and run `IwaraTool.exe`. Data lives in `data/` beside the program, so extracting into the folder of an older version keeps its data. A tray icon and launch at login are available.
- **macOS**: a ZIP for Apple Silicon (ARM64) holds `IwaraTool.app`; drag it into Applications. CI runs on macOS 15; older versions are unverified. The app has no Developer ID signature or notarization; see [Installation and First Steps](./install_en.md) for the first-launch prompt and the proxy.
  - Data lives in `~/Library/Application Support/IwaraTool/`; downloads default to `~/Downloads/IwaraTool/`.
  - Update by replacing the `.app`; your data is kept.
- **Android**: `IwaraTool-Android-<version>.apk`. Every release is signed with the same long-lived key, so a new APK installs over the old one as an update and keeps the data.
- **Linux / macOS Intel**: no prebuilt package for now; run from source (below).

## Supported URL types

```text
https://www.iwara.tv/profile/username
https://www.iwara.tv/profile/username/videos
https://www.iwara.tv/playlist/xxxxxxxx
https://www.iwara.tv/video/xxxxxxxx
https://www.iwara.tv/videos?sort=date
https://www.iwara.tv/videos?tags=2d&sort=likes
https://api.iwara.tv/videos?tags=2d&sort=date
```

`sort` supports: `date`, `trending`, `popularity`, `views`, `likes`.

`tags` supports see the [Tag index](../iwara_tags.md).

## Naming templates

A naming template decides where each video is saved under the download folder. In the rule editor you can pick a preset, or build one by choosing fields and typing the text between them (`_`, `-`, a space, or `/` to start a new folder). The full template stays editable, with a live preview.

| Placeholder | Meaning |
| --- | --- |
| `{author}` | Author account ID (for example `user154126`) |
| `{username}` | Author nickname |
| `{title}` / `{id}` / `{slug}` | Video title / ID / URL slug |
| `{YYYY-MM-DD}` `{date}` / `{YYYY}` / `{MM}` / `{DD}` | Publish date and its parts |
| `{quality}` | Downloaded quality |
| `{views}` `{likes}` `{comments}` | Counters |
| `{duration}` | Length in seconds |
| `{rating}` | Content rating |

`.mp4` is appended automatically. Reserved Windows names, over-long paths and illegal characters are fixed automatically.

## Under the hood

- **Download engine**: valid `X-Version` signature calculation, quality fallback (`Source -> 540 -> 360`), and a stateful scheduler that avoids early URL expiration. Optional aria2 RPC.
- **Reliability**: size and resume verification for downloads, back-off retries and pacing for API requests, SQLite WAL, rotating logs (`data/logs/`).
- **Persistence**: the task queue is saved and recovered after a safe exit; history and subscriptions live in SQLite. See [Local Data and Caches](./data_en.md).
- **Search**: live Iwara API plus the Oreno3D bridge; tag suggestions come from a bundled trilingual dictionary.
- **API notes**: [API.md](../API.md).

## Run / Build from source

Dependencies are managed by [pixi](https://pixi.prefix.dev/latest/).

```shell
pixi install
pixi run start  # Run the application
pixi run build  # Build the application
pixi run crawl  # Crawl tag data (updates docs/iwara_tags.md)
pixi run test   # Run the test suite
```

`pixi run build` packages the desktop app for this system with `flet build` (`--platform apk` builds Android; it needs the Android SDK and normally runs in CI). Android releases are signed with one long-lived key: set `FLET_ANDROID_SIGNING_KEY_STORE` (the .jks path), `FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD` and `FLET_ANDROID_SIGNING_KEY_ALIAS` before building, and the script checks the APK's certificate against `RELEASE_CERT_SHA256` in `scripts/tools/build.py`. CI reads the repository secrets `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD` and `ANDROID_KEY_ALIAS`; a tagged release fails without them.