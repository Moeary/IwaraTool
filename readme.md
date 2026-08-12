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
- Fluent search page with online Oreno3D video/tag search, live Iwara video/author/playlist search, bilingual/trilingual tag suggestions, pagination, grid/list views, and configurable columns.
- Oreno3D is used only as an online search bridge: the app resolves the Iwara ID, hydrates metadata from the Iwara API, and opens the canonical `iwara.tv/video/{id}` page without mirroring the Oreno3D catalog locally.
- Local subscription management with followed-author import, refresh tracking, new-item counts, and list/cover views.
- Named rules for likes, views, date range, tags, title keywords, naming templates, and download behavior.
- Search-only result cap.
- Token cache in `data/config.ini` for faster startup sign-in.
- Runtime language switching (`zh/en/ja`) without restarting.
- Runtime light/dark theme switching.
- Filename template placeholders for flexible naming and directory layout.
- Optional aria2 RPC, thumbnail, and `.nfo` sidecar generation.
- Download rules can generate Emby/Kodi/Jellyfin-friendly `<movie>` NFO sidecars with standard metadata fields plus Iwara IDs and statistics.
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

## Local Data and Caches

Runtime data is stored under `data/` and is separated by purpose:

| Path | Purpose |
| --- | --- |
| `data/config.ini` | Token, UI, concurrency, and download behavior settings |
| `data/history.db` | Download history and local file state |
| `data/subscriptions.db` | Subscription sources, videos, and refresh state |
| `data/rules.json` | Named download rules |
| `data/iwara_tags.json` | Generated offline tag index with localized fields |
| `app/data/tag_translations/loveiwara_iwara_tags_localized.json` | Bundled MIT-licensed LoveIwara translation source |
| `data/tag_translations/loveiwara_iwara_tags_localized.json` | Runtime translation cache, refreshed by “Update Tags” |
| `data/img/search/` | Search-page image cache |
| `data/img/sub/` | Subscription-page video cover cache |
| `data/img/avatar/` | Subscription author avatar cache |

Packaged builds embed the LoveIwara dictionary under `app/data/` and expand it to `data/tag_translations/` beside the executable on first run when no runtime cache exists. Existing runtime caches are kept. Other files from the development machine's `data/` directory are not embedded; keep that directory beside an updated executable to retain local state.

When NFO generation is enabled in a download rule, the NFO is written beside the video with the same base name. New files include standard movie metadata fields for media-center import and retain Iwara-specific aliases; existing NFO files are not rewritten automatically.

The search page keeps Oreno3D results online and only caches the current result images. Oreno3D supports video/tag search in this bridge; switch to the Iwara live API for author or playlist results.

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
