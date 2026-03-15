# IwaraTool API Notes

[简体中文](./API.zh.md) | [日本語](./API.ja.md)

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

## 5. Filters

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

## 6. Input URL Types Supported by UI

- Single video URL:
  - `https://www.iwara.tv/video/{id}`
- User/profile URL:
  - `https://www.iwara.tv/profile/{name}` or `/user/{name}`
- Playlist URL:
  - `https://www.iwara.tv/playlist/{id}`
- API search URL (new):
  - `https://api.iwara.tv/videos?...`
  - `https://www.iwara.tv/videos?...`

## 7. Tag Crawl Script (Preparation for Future One-Click Tag Filter)

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
