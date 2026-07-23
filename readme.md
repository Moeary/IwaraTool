# IwaraTool

![logo](./docs/iwaratool_logo.png)

[简体中文](./readme_zh.md) | [日本語](./readme_ja.md)

Say goodbye to tedious command-line tools! Iwara batches downloader with a modern Fluent-style interface, making it easy for anyone to download all videos from a creator with one click.

![demo](./docs/iwaratool_demo_v0.6.gif)

## Features
- Valid `X-Version` signature calculation for API requests.
- Quality fallback: `Source -> 540 -> 360`.
- Stateful scheduler to avoid early URL expiration.
- Local dedup + SQLite history center.
- Batch enqueue from user profile, playlist, and search URLs.
- Local subscription management with followed-author import, refresh tracking, new-item counts, and list/cover views.
- Named rules for likes, views, date range, tags, title keywords, naming templates, and download behavior.
- Search-only result cap.
- Token cache in `data/config.ini` for faster startup sign-in.
- Runtime language switching (`zh/en/ja`) without restarting.
- Runtime light/dark theme switching.
- Filename template placeholders for flexible naming and directory layout.
- Optional aria2 RPC, thumbnail, and `.nfo` sidecar generation.
- History center with search, filters, sorting, open-file actions, rename, and moved-record cleanup.
- Configurable table columns, persistent widths/order, and responsive split layouts.
- Retry now cleans matching temporary cache files before re-downloading.

## Quick Start
1. Download latest binary from [Releases](https://github.com/Moeary/IwaraTool/releases).
   - Linux binaries are built on GitHub's `ubuntu-latest` runner and do not support older glibc-based systems. For older distributions, download the source and build locally.
2. Open the app. Sign in when downloading private videos or importing followed authors.
3. Paste a URL in `Download Workbench`, choose a rule if needed, and submit it.
4. Use `Subscriptions` to track account feeds, authors, or playlists and batch-download new items.

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
2. Subscriptions

   ![Subscriptions](./docs/panel_view/iwaratool_subscription_panel.jpg)
3. History

   ![History](./docs/panel_view/iwaratool_history_panel.jpg)
4. Rules

   ![Rules](./docs/panel_view/iwaratool_rule_panel.jpg)
5. Settings

   ![Settings](./docs/panel_view/iwaratool_settings_panel.jpg)

## Docs
- Wiki: <https://github.com/Moeary/IwaraTool/wiki>
- API notes (EN): [docs/API.md](./docs/API.md)
- Tag index: [docs/iwara_tags.md](./docs/iwara_tags.md)

## Run / Build

Project dependencies are managed by [pixi](https://pixi.prefix.dev/latest/).

If you are a developer and want to run the source code directly or build the app:

```shell
pixi install
pixi run start  # Run the application
pixi run build  # Build the application
pixi run crawl  # Crawl tag data (updates docs/iwara_tags.md)
pixi run python -m unittest discover -s tests -p "test_*.py" -v
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
