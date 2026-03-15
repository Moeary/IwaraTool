# IwaraTool

![](./docs/iwaratool_logo.png)

[简体中文](./readme.zh.md) | [日本語](./readme.ja.md)

A Python + PySide6 downloader for Iwara, optimized for batch downloads.

![](https://raw.githubusercontent.com/Moeary/pic_bed/main/img/202603051905789.png)

## Features
- Valid `X-Version` signature calculation for API requests.
- Quality fallback: `Source -> 540 -> 360`.
- Stateful scheduler to avoid early URL expiration.
- Local dedup + SQLite history metadata.
- Batch enqueue from user profile, playlist, and search URLs.
- Filters: likes, views, date range, include tags, exclude tags.
- Search-only result cap.
- Token cache in `data/config.ini` for faster startup sign-in.
- Runtime language switching (`zh/en/ja`) without restarting.
- Filename template placeholders with alias support.
- Optional aria2 RPC, thumbnail, and `.nfo` sidecar generation.

## Quick Start
1. Download latest binary from [Releases](https://github.com/Moeary/IwaraTool/releases).
2. Open app and sign in first.
3. Paste URLs in `New Download` and start queueing.

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

## Docs
- Wiki: <https://github.com/Moeary/IwaraTool/wiki>
- API notes (EN): [docs/API.md](./docs/API.md)
- API notes (ZH): [docs/API.zh.md](./docs/API.zh.md)
- API notes (JA): [docs/API.ja.md](./docs/API.ja.md)
- Tag index: [docs/iwara_tags.md](./docs/iwara_tags.md)

## Tag Crawl
```bash
pixi run python app/core/crawl_iwara_tags.py
```

## Run / Build
```bash
pixi run start
pixi run crawl
pixi run build
```

## License
MIT License.
