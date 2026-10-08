# IwaraTool API Notes

[简体中文](./API_zh.md) | [日本語](./API_ja.md)

This document describes the Iwara REST API as IwaraTool uses it, and which feature sends which request. Iwara has no official public API: everything below was read off the live site, the project's own tests, and the open-source clients [LoveIwara](https://github.com/FoxSensei001/LoveIwara) and [gallery-dl](https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/iwara.py). Each endpoint is marked **verified** (exercised against the live API by this project) or **reference** (taken from another client's source and not exercised with a real account here).

Contents: [1 Conventions and authentication](#1-conventions-and-authentication) · [2 Read endpoints](#2-read-endpoints) · [3 Account actions](#3-account-actions) · [4 Which feature sends which request](#4-which-feature-sends-which-request) · [5 Search download](#5-search-download) · [6 Fluent search page](#6-fluent-search-page) · [7 NFO generation](#7-nfo-generation-and-media-center-compatibility) · [8 Filters](#8-filters) · [9 Input URL types](#9-input-url-types-supported-by-ui) · [10 Tag crawl script](#10-tag-crawl-script-and-local-translation-cache)

## 1. Conventions and authentication

### Hosts and headers
- API host: `https://api.iwara.tv`. `https://apiq.iwara.tv` answers the same routes and is used as a fallback for video detail, the following list and the tag crawl.
- Media host: `https://i.iwara.tv` (covers, avatars, image-post pictures).
- Every request carries `Accept: application/json, text/plain, */*`, `Origin: https://www.iwara.tv`, `Referer: https://www.iwara.tv/` and `X-Site: www.iwara.tv`; the HTTP session is `cloudscraper` (Chrome/Windows profile) so Cloudflare's browser check passes.
- Errors come back as JSON `{"message": "errors.xxx"}` (for example `errors.privateVideo`, `errors.notFound`, `errors.badRequest`). The client turns HTTP and network failures into readable messages and never reports a failure as an empty result.

### Pacing and retries
- Requests to one host are spaced at least `request_min_interval_ms` apart (default 200 ms).
- `429`, `502`, `503`, `504` and transient network errors are retried up to `request_max_retries` times (default 2) with exponential back-off (1 s, 2 s, 4 s, … capped at 30 s); a `Retry-After` header is honoured up to 30 s.
- Both settings live under Settings → System & Maintenance → Request pacing. Background work (Home rows, covers, subscription refresh) uses its own HTTP session per worker, carrying the same token and proxy, so independent requests overlap instead of queueing.

### Login — verified
- `POST /user/login`, body `{"email": "<username or email>", "password": "<password>"}`; success returns `{"token": "<jwt>"}`.
- Every authenticated request sends `Authorization: Bearer <token>`.
- The token is cached in `data/config.ini` (`auth_token`, `auth_token_saved_at`). On start the cached token is used first; the account/password login only runs when no token is available.
- Anonymous requests work for public content. Signing in unlocks private videos, the subscription feed, and the account fields below (`liked`, `following`).

## 2. Read endpoints

### Video and image detail — verified
- `GET /video/{id}` — one video. Fields the app reads: `id`, `title`, `body`, `rating` (`general` | `ecchi`), `numLikes`, `numViews`, `numComments`, `createdAt`, `user` (`id`, `name`, `username`, `avatar`), `tags[]` (`id`, `type`), `file` (`id`, `duration`, `height`), `fileUrl`, `thumbnail` (cover index), `embedUrl` (YouTube embeds cannot be downloaded) and, when signed in, `liked`.
- `GET /image/{id}` — one image post: the same fields plus `numImages` and `files[]` (`id`, `name`, `width`, `height`). Pictures are served from `https://i.iwara.tv/image/original/{file.id}/{file.name}` and `https://i.iwara.tv/image/thumbnail/{file.id}/{file.id}.jpg`.
- Covers: `https://{host}/image/original/{file.id}/thumbnail-{NN}.jpg`; Home cards swap `original` for `thumbnail` (a ~10 KB rendition). Avatars: `https://i.iwara.tv/image/avatar/{id}/{name}`.
- `GET /video/{id}/related?limit=12` and `GET /image/{id}/related?limit=12` — related posts (ignore the SFW/NSFW choice; the app filters them locally).
- `GET /video/{id}/comments?page=0&limit=20` and `GET /image/{id}/comments?...` — comments; `&parent={commentId}` returns the replies to one comment. Response: `{count, results[]}`.

### Download sources — verified
- Input: `fileUrl` from the video detail. A `GET` to it with `Authorization` (if signed in) and an `X-Version` header returns a list of `{name, src: {view, download}}`.
- `X-Version = sha1("{file uuid}_{expires}_{salt}")`, where the uuid is the path segment after `/file/` and `expires` comes from the URL's query string. The site rotates the salt now and then, so the client tries the built-in salts and any the user adds (Settings, or `data/x_version_salts.json`).
- Quality fallback: `Source → 540 → 360` starting from the preferred quality. A missing `fileUrl` means a private video (`errors.privateVideo`), an embed or an unavailable post.

### Listings: `/videos` and `/images` — verified
`GET /videos` and `GET /images` share the same parameters:

| Parameter | Meaning |
|---|---|
| `page`, `limit` | Zero-based page and size (the app sends 1–100; Home asks for 24, Search 32, "More" lists 32). |
| `sort` | `date`, `trending`, `popularity`, `views`, `likes`. |
| `rating` | `general` (SFW) or `ecchi` (NSFW); omit for both. |
| `tags` | Comma-separated tag ids; every tag must match. **Plural**: the singular `tag` is ignored by the live API (the app maps old input to `tags`). Not available on `/images`. |
| `user` | A user id (not a username); resolve it with `GET /profile/{username}`. |
| `subscribed=true` | The signed-in account's subscription feed (needs a token). |

Response: `{count, limit, page, results[]}`. `count` is *not* a stable total: a full page reports `(page + 1) × limit + 1`, which the app treats as "there is a next page" rather than showing a page count. Unknown tags can answer HTTP 500, so a server error is never read as "no matches".

### Text search: `/search` — verified
`GET /search?type={videos|images|users|playlists}&query=…&sort=…&page=…&limit=…`

- Sorts per type: `videos` and `images` accept `relevance`, `date`, `views`, `likes`; `users` and `playlists` accept only `relevance` and `date` (`views` / `likes` answer `errors.badRequest`, so the client falls back to relevance).
- `rating` is ignored by this endpoint, so the SFW/NSFW choice is applied to the results locally.
- An empty `query` is not sent: video browsing uses `/videos`, image browsing `/images`.

### Users — verified
- `GET /profile/{username}` → `{user: {id, name, username, avatar, …}}`. Signed in, `user` also carries `following` (you follow them), `followedBy` and `friend` — the app's "Followed on Iwara" state.
- `GET /user/{id}/following?page=…&limit=50` — the accounts the user follows (used by "Import followed authors").

### Playlists — verified
- `GET /playlist/{id}?page={n}` — the videos of a playlist. Playlist cards come from `/search?type=playlists`.

### Tags — verified
- `GET https://apiq.iwara.tv/tags?filter={A-Z0-9}&page={n}` — the tag catalogue (see section 10).

## 3. Account actions

All three need a token; the client refuses to send them signed out and reports "sign in first" instead. Any `2xx` answer counts as success; the body is not used. **Reference** (from the LoveIwara client; the app's tests use a mocked session, not a live account).

| Action | Request |
|---|---|
| Like a video / image | `POST /video/{id}/like` · `POST /image/{id}/like` |
| Remove the like | `DELETE /video/{id}/like` · `DELETE /image/{id}/like` |
| Follow an author on the website | `POST /user/{userId}/followers` |
| Unfollow | `DELETE /user/{userId}/followers` |

- The like state shown on the detail page is `liked` from `GET /video/{id}` / `/image/{id}`; the counter changes locally after a successful click.
- Following on the website is separate from the app's **local subscription** (a row in `data/subscriptions.db`): the author page shows both states side by side and changes them independently. "Import followed authors" copies the website follows into local subscriptions.

## 4. Which feature sends which request

| Feature | Request(s) |
|---|---|
| Home — "My subscriptions" | `/videos?subscribed=true&sort=date[&rating]` and `/images?…` (signed in) |
| Home — hot rows | `/videos?sort=trending\|popularity\|date[&rating]`, `/images?…` |
| Home — tag row | `/videos?tags=<resolved ids>&sort=…[&rating]` (names go through the local tag dictionary first) |
| Home — keyword row | `/search?type=videos\|images&query=…&sort=…` |
| Home — author row, author page | `/profile/{username}` once per session for the id, then `/videos?user={id}&sort=date` / `/images?…` |
| Home — row size and cache | 24 posts per row (page 0). Rows are cached in `data/home_feed_cache.json` per row, tab and rating; the cache key's *signature* is the ordered post ids, so a re-check that returns the same ids leaves the cards alone. Re-check age: Settings → Home (default 15 min, 0 = manual only). |
| "More in Search" | The matching Search-page query (type, tag or keyword, sort); an author row opens the in-app author page |
| Post detail | `/video/{id}` or `/image/{id}`, then `/related`, then `/comments`; Like → `POST|DELETE …/like` |
| Author page / status bar | `/profile/{username}` (follow state) + `/videos?user=…`; Follow → `POST|DELETE /user/{id}/followers` |
| Subscriptions refresh | `/videos?user={id}&sort=date` (incremental: stops at the first known id), `/playlist/{id}`, `/videos?subscribed=true`; covers via `/video/{id}` when a list row has no file id |
| Search page | `/videos`, `/images`, `/search`, `/profile/{username}`, `/playlist/{id}` (see section 6) |
| Download | `/video/{id}` → `fileUrl` → sources (section 2) |

## 5. Search download

The app supports API search URLs directly in the download input box.

### Supported format
- `https://api.iwara.tv/videos?...`
- `https://www.iwara.tv/videos?...`
- Example: `https://api.iwara.tv/videos?tags=2d&sort=date`

The web route and the JSON endpoint both use the plural `tags` (verified 2026-10-03: the singular `tag` is ignored and returns unfiltered results). IwaraTool still accepts the old singular form and normalizes it to `tags` at the API boundary.

### Behavior
- Parses query parameters from the URL
- Fetches paginated results from `GET /videos`
- Auto-enqueues each returned video id

### Result cap (search-only)
- Settings → `Search Download Limit`; config keys `search_limit_enabled` (bool) and `search_limit_count` (int)
- If enabled, only the first `N` videos from the search results are enqueued
- If the URL also includes `limit`, the effective cap is `min(url limit, setting limit)`

## 6. Fluent search page

The sidebar's Search page is an online browsing and batch-enqueue UI, separate from "search URL enqueue" in the download hub. The default source is Oreno3D online search; it can be switched to the Iwara live API. It does not build a local mirror of either site.

### Search types
- **Keyword** — Iwara with a non-empty keyword: `GET /search?type=videos&query=…&sort=…&page=0`, quoted phrases kept verbatim; empty keyword browses `/videos`. Oreno3D: `GET https://oreno3d.com/search?keyword=…&sort=latest&page=1`; opening a result resolves it to its Iwara page.
- **Authors** — by username on the Iwara live API, showing the profile and video count. Free text uses `/search?type=users`.
- **Tags** — Iwara uses `/videos?tags=…`, several tags comma-joined and all required. Full Chinese/Japanese/English names map to tag ids through the dictionary; a name with several ids asks for an explicit pick instead of choosing blindly; unrecognized raw ids are kept unchanged. Oreno3D uses its own tag mapping and falls back to its keyword entry.
- **Images** — `/images`, or `/search?type=images` with a keyword; an empty keyword browses.
- **Playlists** — `/search?type=playlists`, or a playlist id / `/playlist/{id}` link to list its videos.
- Each source and type keeps its own input and sort draft, so switching never submits a title keyword as a tag. History records the raw input and its type.
- **Open author page** (context menu) opens the in-app author page when an Iwara account can be identified (it shows the app-subscription and Iwara-follow state, section 3); otherwise it falls back to the source site's author page. "Open author page in browser" always goes to the source site.

### Sorting and download rules
- Iwara keyword sorts: `date`, `relevance`, `views`, `likes`; video lists and tags: `date`, `trending`, `popularity`, `views`, `likes`. The selector follows the mode.
- Iwara results keep the server order: no re-sorting of a page, no local substring filtering that would drop valid matches found in descriptions. "Next page" reuses the same query and sort; edits not yet submitted restart from page one.
- For `/videos`, `count` may be the "offset + page size + 1" next-page marker; the true total of `/search` is handled separately. When the server uses another page size, only a confirmable next page is kept. HTTP errors and malformed results show their reason instead of looking like zero results.
- The page does not duplicate the Download Hub's advanced filters; its "download rule" picker reuses the existing rules. Choosing a rule applies its metadata filters, naming template, cover/NFO and download behaviour immediately and makes it the default.
- With SFW / NSFW selected, `/search` results are requested 100 at a time and filtered locally, reading up to 4 more pages until about 24 rows match.

### Iwara search verification (2026-10-03)
Read-only requests with the project's anonymous `IwaraAPI` session; no media was downloaded:
- `/videos?q=zz_iwaratool_no_such_query_13` returned the same 4 ids as the unfiltered list; `/search?type=videos&query=zz_iwaratool_no_such_query_13` returned 0. The old keyword parameter was an unfiltered list.
- `animation` on `/search` returned 8 rows on each of the first two pages with the same total (5796) and no duplicate ids; `date`, `views`, `likes` descend by their field, `relevance` differs from date order. The total is only a snapshot.
- `tags=genshin_impact`: all 8 rows carried the tag; with `tag` only 1 did. `tags=genshin_impact,hatsune_miku`: all 16 rows had both. The singular conversion was removed and the batch query entry fixed.
- A non-existent `tags` value can return HTTP 500; a server error is not "zero matches".
- The local dictionary maps 原神 to several ids (`ganshin` came first and gave 0 results with `hatsune_miku`); explicit `genshin_impact,hatsune_miku` works. Submission now checks for ambiguity.

### Image cache
- Cards load covers and avatars on demand. Search images: `data/img/search/`. Subscription covers: `data/img/sub/` (reusing a cover already in the download history, never overwriting a rule-generated one; the old `data/img/sub_video/` is reused when an item is read). Avatars: `data/img/avatar/` (files start with `username`; old `data/img/avatar_*` files are copied over and subscription rows updated).
- When a list endpoint returns only an id, the cover refresh calls `GET /video/{id}` for `file.id`, `fileUrl` and `thumbnail`, then downloads `https://{file-host}/image/original/{file-id}/thumbnail-{index:02d}.jpg`; the resolved URL is written back to the subscription database.
- Covers for what the user is looking at are queued ahead of background ones, and covers that failed are retried the next time they are requested.
- Files are named by URL fingerprint and replaced atomically through a temp file; a network failure leaves the placeholder.

### Refresh and cache concurrency
Settings → Subscriptions & Refresh → "Refresh and cover performance" controls these UI keys:
- `cover_download_workers_v1` — covers fetched in parallel, `1–16`, default `6`.
- `subscription_refresh_workers_v1` — subscription sources refreshed in parallel, `1–8`, default `3`; each uses its own API session with the current token and proxy.
- `subscription_incremental_refresh_v1` — incremental account-feed refresh, on by default. It applies to the account feed and authors imported from the account: pages are read newest-first and stop at the first locally known id (the first run, with no known id, builds the full index). Playlists and local author sources are always fetched in full. Concurrency covers network requests only; SQLite writes stay serialised.
- `home_cache_minutes_v1` — how long a cached Home row is considered fresh (default 15, 0 = refresh only on request).

### Result views
- Grid mode uses the shared cover-size slider (the same control and saved size as Home and Subscriptions); the number of columns follows the available width.
- List mode loads no thumbnails and shows Iwara ID, title, author, views, likes, comments, published date, tags and links in a table; "Fields" customizes them (it reuses the Subscriptions page's column persistence).

### Oreno3D online search
- Only the current page is requested; nothing is copied into a local database apart from the image cache.
- The current page resolves each card's Iwara ID from the Oreno3D detail page on demand, then calls `GET /video/{iwara_id}` for title, author, views, likes, comments, tags and thumbnail. The final link is `https://www.iwara.tv/video/{iwara_id}`; no Iwara web page is parsed and the Oreno3D detail page is never the final page.
- The bridge reports item by item: a card can be opened or queued as soon as its id is known. Settings choose between background pre-resolution and on-click resolution, and the concurrency (1–8).
- Oreno3D keeps its own thumbnail as the only cover (the Iwara thumbnail is recorded but not downloaded again).
- Types follow the source: Oreno3D shows videos and tags; authors and playlists come from the Iwara live API.
- Oreno3D pages hold about 36 cards; the page boundary is kept and navigated with the arrows beside the results toolbar.
- The Oreno3D form is `GET /search` with `keyword`, plus `sort` (`hot`, `favorites`, `latest`, `popularity`) and `page` (from 1). `keyword` is free text over title, author or tag labels; multiple words work with spaces or commas; `#tag` and `|` are ordinary characters. Tag catalogue pages are `/tags`, `/tag-groups/{id}` and `/tags/{id}?sort=latest&page=1`; they are not a `/search` parameter.

### Oreno3D video and author fallback (verified 2026-10-03)
- Some old detail pages have an empty `h1.video-h1` but still carry the source and author links; the parser now continues with the source id instead of a title, while a response without the detail markers stays an error.
- The video source is taken from `.video-figure a` and `a.video-watch-btn2`, checked for an Iwara host and a `/video/{id}` path; comment and sidebar links are never scanned.
- The Oreno3D author id, name and URL travel with the link result, independent of the Iwara metadata request. An id-only or failed response never clears an existing `_iwara_metadata_loaded`.
- When the original video yields no usable Iwara author, the Oreno3D author page is read (up to 8 works) and the `user` of a reachable video gives the account; the same author in one batch reuses one fallback lookup. Manual author actions can use it too.
- Spot checks of pages 1, 1000 and 9500 (14 details) all produced video and author links; page 9600 exposed empty-title records ([117917](https://oreno3d.com/movies/117917), [120989](https://oreno3d.com/movies/120989), [8350](https://oreno3d.com/movies/8350)) that now resolve. 117917's original video answers JSON `403 / errors.privateVideo`, not "deleted".
- If every candidate fails, the Oreno3D author page and the error stay; display names are never guessed to be accounts.

### Multilingual tag suggestions
- The tag box suggests English, Simplified Chinese and Japanese candidates and keeps focus and separators after a click. Results still follow the current source.
- The dictionary uses `data/iwara_tags.json` offline; "Update Tags" caches LoveIwara's MIT dictionary under `data/tag_translations/`. Startup needs no network.
- Third-party sources and licences: [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

## 7. NFO generation and media-center compatibility

When the `NFO` action is enabled in a download rule, the app writes a same-name `.nfo` sidecar next to the video: UTF-8 `<movie>` XML for Emby, Jellyfin and Kodi.

- Titles: `title`, `originaltitle`, `sorttitle`
- Iwara identifiers: `video_id`, `id`, `uniqueid type="iwara"`, `iwaraid`
- Source: `source`, `source_url`, `slug`, `quality`
- Author: `author`, also written as `director` and `studio`
- Dates: `premiered`, `releasedate`, `year`, `published_at`
- Duration: `runtime` (minutes), `duration` (seconds), `fileinfo/streamdetails/video/durationinseconds`
- Rating and statistics: `rating`, `mpaa`, `likes`, `views`, `comments`, plus `iwara_likes`, `iwara_views`, `iwara_comments` aliases
- Description and tags: `plot`, `outline`, repeated `genre`/`tag`, optional `tags_json`
- Cover: `thumb` only when the local cover exists, as a file name in the video's folder

Standard and Iwara-specific fields are written together so media centers recognise them without losing the Iwara data. Existing NFO files are not rewritten at startup; re-download, regenerate metadata or use an external tool to get newly added fields.

## 8. Filters

The global filter switch applies during the resolve stage.

- Min likes, min views, publish date range, include tags, exclude tags.
- Tag matching is case-insensitive; separators are comma, Chinese comma, spaces, `;`, `|`; values are normalized and compared against the API tag fields `id`, `type`, `slug`, `name`, `title`.

## 9. Input URL types supported by UI

- Single video: `https://www.iwara.tv/video/{id}`
- User/profile: `https://www.iwara.tv/profile/{name}` or `/user/{name}`
- Playlist: `https://www.iwara.tv/playlist/{id}`
- API search URL: `https://api.iwara.tv/videos?...` or `https://www.iwara.tv/videos?...`

## 10. Tag crawl script and local translation cache

- Script: `app/core/crawl_iwara_tags.py`. It crawls `https://apiq.iwara.tv/tags?filter={A-Z0-9}&page={n}` and exports a three-language-ready dataset to `data/iwara_tags.json` and `docs/iwara_tags.md`.
- Each tag gets `name_en`, `name_zh`, `name_ja` (default: the original text; override with `--translation-map path/to/map.json`).
- Example: `pixi run python app/core/crawl_iwara_tags.py`

Local translation cache:
- Project-generated offline index: `data/iwara_tags.json`; LoveIwara MIT cache: `data/tag_translations/loveiwara_iwara_tags_localized.json`.
- The search page loads the LoveIwara cache first and falls back to `data/iwara_tags.json`. "Update Tags" downloads on demand; startup needs no network.
- Bundled sources: `app/data/tag_translations/loveiwara_iwara_tags_localized.json` and the Oreno3D/Iwara label map `app/data/oreno3d_iwara_map.json`; Nuitka and GitHub Actions include both explicitly.
- On first run the bundled dictionary is expanded to `data/tag_translations/` beside the executable only when the runtime cache is missing; existing user caches are never replaced.
