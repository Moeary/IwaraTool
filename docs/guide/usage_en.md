# Usage Guide

[简体中文](./usage.md) | [日本語](./usage_ja.md) | [Back to README](../../readme.md)

Details that go beyond the README's quick start.

## Layout

On a wide window the sidebar on the left holds the five main pages at the top — Home, Subscriptions, Search, Downloads and Library — and Tools, Settings and the account at its foot. Drag the sidebar's right edge to widen it (with labels) or narrow it; the width is remembered, and the button at the top folds it away. Narrow windows and phones use a bottom bar instead, and the menu button at the top left (or a swipe from the left edge) opens a drawer laid out like the sidebar.

Tools is a hub: download rules, the Repair Center, the upload compressor and the cover preview are listed on the left and the chosen one is shown on the right. Phones have no Repair or Tools; rules and settings open from the account panel as full-screen pages.

The shield button at the top of the sidebar shows the content rating (All / SFW / NSFW) and switches it. The account entry opens the "Account and preferences" panel for signing in, the site, the rating and the theme.

## Home and content rating

Home shows "My subscriptions" (needs sign-in; videos or images), "Hot videos" and "Hot images" by default. Click a row's title to fold it; each row shows when it was updated, and the hot rows switch between trending, popular and newest. A row's "⋮" menu refreshes it or downloads what it shows with the chosen rule; "More in Search" opens Search with the matching type, tag or keyword and sort, and "More" on My subscriptions opens the full paged grid.

"⋮ → Customize rows…" in the page header (or Settings → Browsing → Home rows) opens the row editor: show or hide rows, move them, edit or delete them, and put anything Search can find on Home — for example "tag hmv · newest", an author's latest works or a keyword's results, each as videos or images with its own sort. Rows are cached and appear at once; they are checked again in the background only after the time set in Settings → Storage → Home cache (15 minutes by default; 0 checks only on a manual refresh). Switching pages never refreshes them.

Click a cover to open its page: the built-in player, statistics, Like, the author card (author page, subscribe in the app, follow on Iwara), the description, clickable tags, related posts and comments; on wide windows the related posts sit in a column on the right. Image posts show the whole set. What a click or double-click on a cover does is set in Settings → Browsing → Clicks on covers. Home, Search and Subscriptions share the cover-size slider in the page header (Ctrl+= / Ctrl+- / Ctrl+0).

The content rating applies to Home and Iwara search and is remembered (Ctrl+Shift+R cycles it). `/videos` and `/images` are filtered by the server with `rating=general|ecchi`; text search (`/search`) ignores the parameter, so with SFW / NSFW each request takes 100 results and filters them locally, reading further pages (up to 4) while fewer than about 24 remain. Oreno3D results carry no rating. The page shown at launch is chosen in Settings → System → Window and startup (Home by default).

## Shortcuts and Back

Every shortcut is listed and can be changed in Settings → Keyboard shortcuts: click a button and press the new keys; Esc cancels, Backspace clears. Defaults: Ctrl+1 to Ctrl+9 go to Home, Subscriptions, Search, Downloads, Repair, Library, Rules, Settings and Tools; Ctrl+, opens Settings; Ctrl+Tab / Ctrl+Shift+Tab switch pages; Esc or Alt+← goes back; Ctrl+Shift+R cycles the content rating; Ctrl+Shift+S switches between iwara.tv and iwara.ai; Ctrl+Shift+L toggles dark mode; Ctrl+= / Ctrl+- / Ctrl+0 change the cover size; F11 toggles full screen; Ctrl+Q quits.

**macOS**: Ctrl above is ⌘ (Command) on a Mac, and a few defaults follow Mac habits: ⌃Tab / ⌃⇧Tab switch pages, ⌃⌘F toggles full screen, ⌘W closes the window and ⌘M minimizes it. Settings shows the ⌘ ⌥ ⌃ ⇧ symbols. A two-finger swipe to the right on the trackpad goes back (not when the content under the pointer scrolls sideways itself).

Back (the arrow in the page header, Esc, Alt+←, the Mac trackpad swipe or a phone's back key) is remembered per page: open a post from Home, wander to Search through the sidebar and come back, and Back still returns to Home's list. Switching pages with the sidebar or Ctrl+1… is not a step; when you arrive from another page (a search result's details, an author page), Back returns to where you came from.

## iwara.tv and iwara.ai

Both sites share one account. Switch with Ctrl+Shift+S (⌘⇧S on a Mac), in the account panel or in Settings → Browsing → Site (iwara.tv by default); the choice is remembered. Home, Search and the "More" lists then browse the new site, each site keeping its own Home cache. Oreno3D only indexes iwara.tv, so on iwara.ai Search uses Iwara. Posts already on screen (an open details page, subscription and library entries, download tasks) keep opening, playing, liking and downloading from their own site. Pasted links are handled by their own domain. A subscription remembers the site it was added on, so the same author can be subscribed on both; Subscriptions lists the current site's, while the timed background refresh checks every subscription on its own site.

## Downloads

Paste one or more video, author, playlist or search links into the field at the top (the button inside it pastes the clipboard) and click Download. A sheet lists what will be queued and with which rule; change the rule there and tick "Remember for this page" if you like. Tasks are rows with cover, title, progress and state, grouped as All / Downloading / Queued / Failed / Completed / Cancelled and filterable by keyword. The header's "⋮" menu retries failed tasks, cancels all, restores cancelled ones and clears completed ones; the log button opens the download log. Clicking a completed task opens its folder or player, as set in Settings → Downloads → Download behavior.

The built-in downloader fetches files over 8 MiB from servers that support ranges over several connections at once (Settings → Downloads → Concurrent downloads → Connections per file, 4 by default) and resumes part by part after an interruption or failure; aria2 downloads are not affected.

## Subscriptions

Subscriptions holds authors and playlists (the account's feed is "My subscriptions" on Home). A wide window shows two panes: on the left the subscriptions (avatar, name, not-downloaded count and a "new" badge; searchable and sortable), on the right the chosen one's works as a grid with title search, a state filter, sort and cover size. "Download shown / Download selected" picks a rule in the download sheet; everything else (download new, select all, mark as seen, mark as moved, restore moved, refresh, author page, delete the subscription) is in the "⋮" menu. Drag the divider between the panes; its position is remembered. On phones an overview lists each subscription with its latest works; tap one to open it. The header adds subscriptions, refreshes all and imports the authors you follow on Iwara.

Covers of subscription works are cached locally by themselves: when the API returns only a stub without a cover address, the work's details are fetched for the cover and the address is remembered.

## Library

The Library lists every download record as a list or as covers: on wide windows the list is a table (columns are chosen by the available width; click a header to sort), on phones rows with thumbnails. Search titles, authors or all fields, filter by state, choose a scope (all, recently downloaded, unwatched, by author) and filter by tags — read from the NFO beside the video (`<file name>.nfo`, or `movie.nfo` in the same folder) or, without one, from the Iwara tags saved at download.

Selecting a record shows it on the right (its own page on phones): a cover stage (enlarge it, page through), a play button that plays the local copy in the built-in player, Previous / Next and Autoplay (following the current filter and sort), and the NFO's tags, plot and people. Its "Manage" menu marks it watched or unwatched, edits the details, renames the file, relocates it (after the file was moved by hand) and deletes the record. A deleted record leaves the list at once and can be restored from the notice for 6 seconds. Long-press a downloaded video to play it in the app or in an external player. The header's "⋮" menu exports CSV, clears records of moved files and more.

## Rules per page

Each page uses its own download rule: change the rule in the download sheet and tick "Remember for this page" to change only that page. "Rule per page" at the top of Tools → Download rules sets the rule of Subscriptions, Search, video details, Home and Downloads links in one place. Pages set to "Follow the default" use the default rule marked ● ("Set as default" in the rule editor).

The rule editor has the rule selector and a New button at the top, then the name, the filename template (a sample file name, the editable full template, the Presets and Insert a field menus and Validate), the metadata filters (likes, views, date, tags, title keywords) and more; switching rules with unsaved changes asks first. Tag suggestions in Chinese, English and Japanese appear under the tag field.

The first start adds an editable "Temporary download" rule: it saves to `temp_video/{title}_{id}.mp4` in the download folder and writes no history. Search and video details use it by default; Subscriptions, Home and Downloads follow the default rule. Temporary downloads do not count as downloaded, but playing from a details page still finds them first instead of streaming.

## Author pages, subscription state and likes

"Author page" from a search result, a Home card, a details page or Subscriptions opens the author page in the app (video and image tabs, paging, batch download of a selection). The author page, a subscription's details and the details page's author card show two badges — "Subscribed in this app / Not subscribed in this app" and "Followed on Iwara / Not followed on Iwara / Not signed in" — with buttons to subscribe or unsubscribe in the app and to follow or unfollow on Iwara. Oreno3D results whose Iwara account is unknown can still open the source site's author page in the browser.

The details page's Like button likes or un-likes a video or image post after sign-in, updating the count at once.

## Repair Center

Tools → Repair changes existing videos in five steps: folder → naming rule and options → scan and preview → run → result. "Rename in place" (the default) keeps each file in its folder and applies only the file-name part of the rule; to move files by folder rules such as `{author}/`, choose "Organize into an output folder" (empty means the scanned folder). Scan results arrive in batches, each row with a state badge and "original path → target path"; ✎ looks a row up again by Iwara ID or link. Conflicts are skipped and existing files are never overwritten. To only add download records, turn off renaming, covers and NFO and keep "Add to history". Pick folders with Browse or type the path (drag and drop is not supported).

## Search

Pick the source (Iwara / Oreno3D) and type keywords at the top; the scope chips below switch between videos, images, authors, tags and playlists, next to the sort, Filter and the cover size. The clock button in the header opens the search history.

- **Keywords and tags**: Iwara's keyword and tag searches keep their own input and sort. Keywords sort by newest, relevance, views or likes; quote an exact phrase; leave it empty to browse. In the Tags scope, suggestions in Chinese, English and Japanese appear under the field; separate several tags with commas to find videos that have all of them.
- **Download marks**: from the local history and whether the file exists: "Downloaded" while the file is there, "Moved" when its path is gone, "On NAS" when a remote aria2 saved it; Oreno3D results are checked once their Iwara ID is known.
- **Authors**: author results open the author page in the app.

### Filtering after a search

Iwara's search API cannot restrict likes, views or duration up front, so Search filters afterwards: click Filter once results are in and set ranges for likes, views and duration (minutes), the upload time (from 24 hours to a year), a minimum like ratio (likes ÷ views), the orientation (portrait / landscape, judged by the video's resolution) and tags to exclude, and re-sort by likes, views, like ratio, duration or upload time; empty means no limit.

Filters apply to the results loaded on the current page and carry over when paging; the button shows how many are set and the summary reads "Found N · M after filters". Oreno3D results join once their Iwara data is resolved. Author and playlist results cannot be filtered.

### Oreno3D tag bridge

The Oreno3D bridge opens a single tag directly at `/tags/{id}` and intersects several tags on the client; `tag:<id>`, `origin:<id>`, `character:<id>` or the matching Oreno3D URL also work. Results are resolved to real Iwara videos before they open or join the queue. When an Oreno3D video's original is deleted or unreachable, the author's other works are tried to find the Iwara author; without a verifiable account the Oreno3D author page is kept rather than guessed from a display name. Tag IDs: see the [tag index](../iwara_tags.md).

## One action menu everywhere

Video menus on Home, Search, Subscriptions, author pages, a details page's related posts and the Library share one order: View details → Play → Open folder / Copy file path → Download → Open in browser / Copy link → author actions → this page's own items. Right-click on the desktop; long-press on phones for an action sheet (with "Select"). Play reads "Play local file", "Play remote file" or "Play online" depending on where the file is; downloaded videos also offer "Play in the app / Play in an external player". "Open folder" uses the real path from the download history and is hidden when the file has moved or was never recorded.

## Downloading to a NAS / remote server (aria2)

Enable aria2 RPC in Settings → Downloads → aria2, turn on "aria2 runs on another device (NAS / server)" and fill in:

- **Download folder on the aria2 device** (required): the path as aria2's machine sees it, such as `/volume1/iwara`. Filename templates apply as usual; videos are written there by the remote aria2, never into the local download folder, and there is no fallback to the built-in downloader on failure.
- **Local mount path** (optional): where the same folder is mounted here over SMB / NFS, such as `/Volumes/iwara` or `Z:\iwara`. It lets you open folders and play videos here and writes covers and NFO beside the videos.
- **HTTP address** (optional): an HTTP address the NAS serves the folder at, such as `http://nas:8080/iwara`. Without a mount, the built-in player or a custom player command (such as mpv) streams from it.

Remote downloads are marked "On NAS", apart from "Downloaded" and "Moved": they are never treated as local files and "Clear moved records" leaves them alone. In remote mode videos already on the NAS are skipped. Covers are always cached here; without a mount NFO is skipped and the remote aria2 saves the cover beside the video. "Test connection" checks that aria2 answers and the mount path works. This is the recommended setup on phones: the phone only browses and submits, and the other device downloads without the phone's background limits.

## Settings

Settings is grouped by purpose: Appearance (theme, language, font), Browsing (site, content rating, Home rows, clicks on covers, player), Account and network (sign-in, proxy, request pacing), Downloads (quality, behavior, concurrency, policy, aria2), Storage (download folder, data folder, Home cache, backup / restore / logs / import from an older version), Search, Subscriptions, System (window and startup, FFmpeg, cover and refresh concurrency), Keyboard shortcuts, and About at the foot of the list (version, licence, links and update checks). Every change is saved at once; text and numbers are saved when you leave the field or press Enter, and the proxy needs Apply.

**Interface font**: Settings → Appearance → Interface font is "Automatic" by default and picks the system UI fonts for the platform and interface language, such as Segoe UI + Microsoft YaHei UI on Windows and the system font + PingFang on macOS; the Japanese interface puts Japanese fonts before Chinese ones. You can also type the name of any installed font; characters it lacks fall back to the platform and system fonts. The fonts in use are listed below, and "Automatic" restores the default.

**Window and startup** (desktop): with "Keep running when the window is closed" on, closing the window minimizes to the tray on Windows (click the icon to show it, right-click to quit) and to the Dock on macOS. Launch at login and system notifications are set here too (finished and failed downloads are combined into one notice, shown in the app while its window is in front).

## Tools

Tools → Upload compressor and Cover preview run ffmpeg. With the path left empty it is looked up on PATH and in the usual install folders (Homebrew, winget, scoop, Chocolatey, the app's folder); choose it in Settings → System → FFmpeg otherwise. Install it with `brew install ffmpeg` on macOS or `winget install ffmpeg` on Windows.

- **Upload compressor**: Iwara accepts H.264 + AAC in MP4, at least 720p and at most 300 MB (600 MB for members). After you pick a video it first says what is needed: a compliant file is ready to upload; a file with only the wrong container or audio is remuxed (the picture is not re-encoded); everything else is re-encoded at a bitrate worked out from "limit × (1 − margin)". x264 two-pass is the default; hardware encoders (VideoToolbox / NVENC / Quick Sync / AMF) are faster and re-encode at a lower bitrate if the result is too large. Videos shorter than 720 on the short side are upscaled to 720p; when the size budget cannot carry the original resolution it drops to 1080p or 720p, which the compression settings can turn off. The output goes beside the source as `name_iwara.mp4`.
- **Cover preview**: after an upload Iwara offers 16 screenshots as the cover, apparently taken at the middle of 16 equal slices (duration × (2i+1)/32; a community finding, not confirmed by Iwara). Load a video to see the 16, numbered in the order of Iwara's picker; they are cropped to 3:2 like a card by default, which you can turn off to see the full frame. Move with the arrow keys, save a full-size screenshot or copy the timestamp. After compressing, run it on the finished file.

## Android

The phone app browses, searches, subscribes, downloads and plays; desktop features such as the Repair Center, Tools and opening folders are hidden. Downloads go to `Android/data/io.github.moeary.iwaratool/files/Downloads` (visible in file managers, removed with the app). Coming back to the app with a freshly copied Iwara post or author link on the clipboard offers to open it. Every release carries the same signature, so a newer APK installs over the old one as an update.
