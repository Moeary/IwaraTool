# Usage Guide

[简体中文](./usage.md) | [日本語](./usage_ja.md) | [Back to README](../../readme.md)

This guide expands on the Quick Start section of the README.

## Home and Content Rating

Home is the first page and, like iwara.tv, shows "My subscriptions" (sign-in required; videos or images), "Hot videos" and "Hot images", each switchable between Trending, Popular and Newest. A click opens the post inside the app: cover and playback, stats, the uploader (view their works or subscribe), description, clickable tags, related posts and comments; image posts show all their pictures. "More" beside a row opens a paginated list whose cards can be ticked and queued in bulk with the chosen download rule; each video row also has "Download shown". Right-click a card to play, download, open in the browser, view the author's works or subscribe.

"Content" (All / SFW / NSFW) at the top applies to Home and Iwara search and is remembered. `/videos` and `/images` are filtered by the server with `rating=general|ecchi`; the text `/search` endpoint ignores that parameter, so with SFW / NSFW it requests 100 results at a time and filters them locally, reading further pages (up to 4) until about 24 rows match; Next continues after the last page read. Oreno3D results carry no rating, so the selector is hidden there. The Search page's Images scope can now browse with an empty keyword, and "View details" in a result's menu opens it on the Home detail page. The sidebar order is Home, Search, Download Hub, Repair, Subscriptions, History, matching Ctrl+1 to Ctrl+6 (configurable in Settings).

## Repair Center

To rename existing videos in bulk, select a folder and naming rule in Repair Center, then scan it. The default in-place mode keeps each video's parent folder and uses only the filename part of the rule. Choose the output-folder mode to move files using directory patterns such as `{author}/`; a blank output uses the scan root. Review the full source and destination paths before applying. Conflicting items are skipped without overwriting existing targets. To import download history only, turn off renaming, covers, and NFO generation and keep the history option enabled.

## Search Download Status

Search status reflects local history and file availability: a recorded file that still exists is marked as downloaded locally, while a missing path is marked as moved. Oreno3D results are checked after their Iwara ID is resolved. Viewing an author's works does not add a subscription; "Add subscription and go" selects that author's subscription source.

## Oreno3D Author Fallback

If an Oreno3D result's original Iwara video is deleted or inaccessible, automatic hydration looks for the Iwara author through other works on the source author page. In on-demand mode, author actions trigger this lookup. If no account can be verified, the Oreno3D author page remains available; display names are never guessed to be Iwara accounts.

## Iwara Keyword and Tag Search

Iwara keyword and tag searches keep separate input and sort settings. Keyword search supports newest, relevance, views, and likes; use double quotes for exact phrases or leave the input blank to browse videos. Tag search accepts suggestions or exact translated names in English, Chinese, and Japanese; separate multiple tags with commas to find videos containing all of them. Sorting and pagination use the server results, and failures remain visible.

## Oreno3D Tag Bridge

In tag scope, a single label uses the direct `/tags/{id}` index, while multiple labels are intersected client-side; `tag:<id>`, `origin:<id>`, `character:<id>`, and Oreno3D entity URLs are also accepted. Oreno3D supports video and tag search in this bridge; for author or playlist results, switch to the Iwara live API. Results are resolved to the canonical Iwara video before opening or queueing. For tag IDs, see the [Tag index](../iwara_tags.md).

## Subscription Cover Cache

Subscription covers are saved locally automatically. To cache an author's covers in advance, first select that subscription source and refresh its video list, then use "Refresh Actions → Cache All Source Covers". This covers all videos recorded for that source, regardless of the current filter or list/cover view, and does not download videos. Running it again reuses existing covers and retries missing ones. Obtaining covers for the first time still requires network access; when it finishes, the success and failure counts are shown.
