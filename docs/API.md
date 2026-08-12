# IwaraTool API Notes

[简体中文](./API_zh.md) | [日本語](./API_ja.md)

This document describes API capabilities currently implemented in this project.

## 1. Authentication

### Login
- Endpoint: `POST https://api.iwara.tv/user/login`
- Body:
  - `email`: username or email
  - `password`: account password
- Success:
  - Returns `token` (Bearer token)

### Token usage
- All authenticated requests use:
  - `Authorization: Bearer <token>`
- Token is cached locally in `data/config.ini`:
  - `auth_token`
  - `auth_token_saved_at`
- Startup behavior:
  - Prefer cached token first (fast path)
  - Fallback to account/password login only when no token is available

## 2. Video Info & Download URL Resolve

### Fetch video metadata
- Endpoint: `GET https://api.iwara.tv/video/{video_id}`

### Resolve downloadable sources
- Input: `fileUrl` from video metadata
- Adds `X-Version` header computed from:
  - `{filename}_{expires}_{salt}`
  - SHA1 hash
- Built-in quality fallback:
  - `Source -> 540 -> 360`

## 3. Batch Sources

### By user
- Resolve user id:
  - `GET https://api.iwara.tv/profile/{username}`
- Fetch video list:
  - `GET https://api.iwara.tv/videos?user={user_id}&sort=date&page={n}`

### By playlist
- `GET https://api.iwara.tv/playlist/{playlist_id}?page={n}`

## 4. Search Download (New)

The app now supports API search URLs directly from the download input box.

### Supported format
- `https://api.iwara.tv/videos?...`
- `https://www.iwara.tv/videos?...`
- Example:
  - `https://api.iwara.tv/videos?tags=2d&sort=date`
  - `https://www.iwara.tv/videos?tags=2d&sort=date`

### Behavior
- Parses query parameters from the URL
- Fetches paginated results from `GET /videos`
- Auto-enqueues each returned video id

### Result cap (search-only)
- Settings -> `Search Download Limit`
- Config keys:
  - `search_limit_enabled` (bool)
  - `search_limit_count` (int)
- If enabled, only the first `N` videos from search results are enqueued
- If URL also includes `limit`, effective cap is `min(url limit, setting limit)`

## 5. Fluent Search Page

The search page is an online browsing and batch-enqueue UI. It does not build a local mirror of the Oreno3D catalog.

- Oreno3D mode supports online video/tag search through its `/search` page, normally 36 cards per page, with `keyword`, `sort`, and `page` parameters.
- Iwara live API mode supports videos, authors, tags, and playlists.
- Oreno3D cards resolve their Iwara ID asynchronously, hydrate title/author/statistics/tags from `GET /video/{id}`, and open `https://www.iwara.tv/video/{id}` as the final URL.
- Grid mode supports a user-selected column count; list mode avoids thumbnails and exposes configurable fields.
- Search images are cached under `data/img/search/`; Oreno3D thumbnails remain the only cover shown for Oreno3D cards to avoid duplicate image downloads.
- Settings control Oreno3D ID resolution timing/concurrency, cover-download workers, and incremental subscription refresh workers.

## 6. NFO Generation and Media-Center Compatibility

When the `NFO` action is enabled in a download rule, the app writes a same-name `.nfo` sidecar next to the video. It is UTF-8 `<movie>` XML intended for Emby, Jellyfin, and Kodi.

The generated file contains standard fields such as `title`, `originaltitle`, `premiered`, `runtime`, `rating`, `genre`, `tag`, `thumb`, and `fileinfo/streamdetails`, plus Iwara identifiers and statistics (`video_id`, `uniqueid`, `iwaraid`, `likes`, `views`, `comments`, and their `iwara_*` aliases). Existing NFO files are not rewritten automatically after an upgrade.

## 7. Filters

Global filter switch applies during resolve stage.

### Supported filters
- Min likes
- Min views
- Publish date range
- Include tags (new)
- Exclude tags (new)

### Tag matching rules
- Case-insensitive
- Input supports separators:
  - comma, Chinese comma, spaces, `;`, `|`
- Normalizes and compares against API tag fields:
  - `id`, `type`, `slug`, `name`, `title`

## 8. Input URL Types Supported by UI

- Single video URL:
  - `https://www.iwara.tv/video/{id}`
- User/profile URL:
  - `https://www.iwara.tv/profile/{name}` or `/user/{name}`
- Playlist URL:
  - `https://www.iwara.tv/playlist/{id}`
- API search URL (new):
  - `https://api.iwara.tv/videos?...`
  - `https://www.iwara.tv/videos?...`

## 9. Tag Crawl Script (Preparation for Future One-Click Tag Filter)

- Script path:
  - `app/core/crawl_iwara_tags.py`
- Purpose:
  - Crawl tags directly from `/tags` endpoint using `A-Z` and `0-9` filters
  - Export tri-language-ready dataset to JSON + Markdown
- Endpoint pattern:
  - `https://apiq.iwara.tv/tags?filter={A-Z0-9}&page={n}`
- Default outputs:
  - `data/iwara_tags.json`
  - `docs/iwara_tags.md`
- Translation fields:
  - `name_en`, `name_zh`, `name_ja` are generated for each tag
  - default is fallback to original tag text
  - can override via `--translation-map path/to/map.json`
- Example:
  - `pixi run python app/core/crawl_iwara_tags.py`

### Local translation cache

- Project-generated offline index: `data/iwara_tags.json`
- LoveIwara MIT translation cache: `data/tag_translations/loveiwara_iwara_tags_localized.json`
- The search page loads the cached LoveIwara dictionary first and falls back to `data/iwara_tags.json`.
- “Update Tags” downloads the dictionary on demand; startup does not require a network request.
- The bundled source is `app/data/tag_translations/loveiwara_iwara_tags_localized.json`; Nuitka and GitHub Actions include it explicitly.
- On first run, the bundled source is expanded to `data/tag_translations/` beside the executable only when the runtime cache is missing. Existing user caches are not replaced.
