# IwaraTool

![logo](./docs/iwaratool_logo.png)

[简体中文](./readme_zh.md) | [日本語](./readme_ja.md)

Say goodbye to tedious command-line tools! Iwara batches downloader with a modern Fluent-style interface, making it easy for anyone to download all videos from a creator with one click.

![demo](./docs/iwaratool_demo_v0.6.gif)

Docs: [Wiki](https://github.com/Moeary/IwaraTool/wiki) · [API notes](./docs/API.md) · [Tag index](./docs/iwara_tags.md)

## Features
- Batch enqueue from user profile, playlist, and search URLs; the task queue is persistent and recovers automatically after a safe exit.
- Download engine: valid `X-Version` signature calculation, quality fallback (`Source -> 540 -> 360`), and a stateful scheduler that avoids early URL expiration.
- Local dedup and a SQLite history center with search, filters, sorting, and cleanup.
- Search: live Iwara video/author/playlist search, Oreno3D online video search, and trilingual tag suggestions; results show local download status.
- Video preview: double-click a video anywhere to play it. Downloaded files open in the player you choose (system default, built-in, or a custom command); videos not on disk or already moved stream in a built-in window with play/pause, seeking, speed, volume, fullscreen and hold-to-speed-up.
- Subscriptions: authors and playlists with refresh tracking and new-item counts; the default overview lists each one with its avatar and latest videos, opens into a poster grid, and the classic table is one click away.
- Named rules for likes, views, dates, tags, title keywords, naming templates, and download behavior; NFO sidecar generation.
- Home: customizable rows (newest, your subscriptions, hot lists, or any tag / keyword / author search), cached on disk and refreshed only when posts change; in-app detail pages, Like and bulk download; a SFW / NSFW content filter applies to Home and Search.
- Author pages: open an author inside the app from Search or a detail page, see whether you subscribe in the app or follow on Iwara, and subscribe / follow right there.
- Repair Center: batch-rename existing videos in place or organize them into an output folder, with destination conflict checks.
- Runtime switching between zh/en/ja and light/dark themes; table columns are configurable and persistent.
- Optional aria2 RPC, thumbnail, and `.nfo` sidecar generation.
- Reliability: size and resume verification for downloads, back-off retries and pacing for API requests, SQLite WAL, rotating logs (`data/logs/`).
- Maintenance: system tray and launch at login, credential-free data backup/restore, SHA-256 verified one-click updates (packaged Windows build).

## Quick Start
1. Download latest binary from [Releases](https://github.com/Moeary/IwaraTool/releases).
   - Linux binaries are built on GitHub's `ubuntu-latest` runner and do not support older glibc-based systems. For older distributions, download the source and build locally.
2. Open the app. Sign in when downloading private videos or importing followed authors.
3. Paste a URL in `Download Workbench`, choose a rule if needed, and submit it.
4. Use `Subscriptions` to track authors or playlists (your account feed is "My subscriptions" on Home) and batch-download new items.

For more detail (Repair Center, search download status, Oreno3D author fallback, keyword/tag search, subscription cover cache), see the [Usage Guide](./docs/guide/usage_en.md). For local data and caches, see [Local Data and Caches](./docs/guide/data_en.md).

## Supported URL Types
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

`tags` supports see [Tag index](./docs/iwara_tags.md).

## Screenshots

1. Download Workbench

   ![Download Workbench](./docs/panel_view/iwaratool_download_panel.jpg)
2. Search

   ![Search](./docs/panel_view/iwaratool_search_panel.jpg)
3. Subscriptions

   ![Subscriptions](./docs/panel_view/iwaratool_subscription_panel.jpg)
4. History

   ![History](./docs/panel_view/iwaratool_history_panel.jpg)
5. Rules

   ![Rules](./docs/panel_view/iwaratool_rule_panel.jpg)
6. Settings

   ![Settings](./docs/panel_view/iwaratool_settings_panel.jpg)

## Run / Build

Project dependencies are managed by [pixi](https://pixi.prefix.dev/latest/).

If you are a developer and want to run the source code directly or build the app:

```shell
pixi install
pixi run start  # Run the application
pixi run build  # Build the application
pixi run crawl  # Crawl tag data (updates docs/iwara_tags.md)
pixi run test   # Run the test suite
```

## Contributing

Pull Requests are very welcome!
Please target the `dev` branch, keep English/Simplified Chinese/Japanese UI text in sync, and run the test suite before submitting. UI changes should include a screenshot or short recording when practical.

## License

MIT License.

**By downloading this program, you agree to comply with the MIT License. See the LICENSE file for details.**

1. Using this project for any illegal, regulatory-violating purpose or any purpose against public order and good morals is prohibited; the user bears full responsibility for losses caused by non-compliant use.
2. The packaged versions and scripts provided by the project are for personal learning and research only, and may not be used for commercial redistribution or resale without permission.
3. Project maintainers reserve the right to update, suspend, or terminate services and support at any time in accordance with laws and regulations or community feedback.

## Special Thanks

Thanks to [hare1039](https://github.com/hare1039)'s [iwara-dl](https://github.com/hare1039/iwara-dl/tree/master) project for its valuable reference and inspiration, especially in the implementation details of X-Version signature calculation and download link parsing, which played a key role in the development of this project.
