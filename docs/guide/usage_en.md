# Usage Guide

[简体中文](./usage.md) | [日本語](./usage_ja.md) | [Back to README](../../readme.md)

This guide expands on the Quick Start section of the README.

## Home and Content Rating

Home is the first page. By default it shows "Latest", "My subscriptions" (sign-in required; videos or images), "Hot videos" and "Hot images". Every row folds away (click its title), shows when it was last updated and has its own refresh button; the hot rows switch between Trending, Popular and Newest. The pencil icon in the header (Ctrl+E), or Settings → Home, customizes the rows: add, remove, re-order or temporarily hide them, and put anything the Search page can find on Home — a row of "tag hmv · newest", the latest works of one author, the results of a keyword; each row picks videos or images and its order. "Reset to default" restores the original four. Rows are cached on disk and shown instantly; they only ask the site again in the background once older than the time set under Settings → Home (15 minutes by default, 0 = only when you refresh), and the cards change only if the posts really changed. Switching pages never triggers a refresh, and while cards are selected, new posts wait behind a "New posts — show" link instead of pulling cards away. The account feed, every sort tab and every content rating are cached separately; signing in or out only discards account-bound rows. A click opens the post inside the app: cover and playback, stats, a Like button, the author (author page, their works, subscription state), description, clickable tags, related posts and comments; image posts show all their pictures. "More in Search" on hot or custom rows jumps to the Search page with the matching type, tag or keyword and order (an author row opens the in-app author page); paging, the list view and bulk queueing happen there. "More" on "My subscriptions" opens a paginated list whose cards can be ticked and queued in bulk with the chosen download rule. Each video row also has "Download shown". Home, Search, Subscriptions and that list share one "cover size" slider (it replaces the Search page's old "Columns" box). Placeholder cards show while loading, covers fade in and cards zoom slightly on hover. Right-click a card to play, download, open in the browser, open the author page, view the author's works or subscribe.

"Content" (All / SFW / NSFW) at the top applies to Home and Iwara search and is remembered. `/videos` and `/images` are filtered by the server with `rating=general|ecchi`; the text `/search` endpoint ignores that parameter, so with SFW / NSFW it requests 100 results at a time and filters them locally, reading further pages (up to 4) until about 24 rows match; Next continues after the last page read. Oreno3D results carry no rating, so the selector is hidden there. The Search page's Images scope can now browse with an empty keyword, and "View details" in a result's menu opens it on the Home detail page. The sidebar order is Home, Subscriptions, Search, Download Hub, Repair, History, matching Ctrl+1 to Ctrl+6 (Rules and Settings are Ctrl+7 and Ctrl+8; all configurable in Settings). The page shown at launch is chosen under Settings → Window & Startup → Page shown at launch (Home by default). On wide windows the detail page stretches the player to the available width and moves related posts into a column on the right; opening a detail page from another page (for example Subscriptions) makes Back return there.

## Subscriptions

The Subscriptions page now keeps only authors and playlists (the account feed is replaced by "My subscriptions" on Home; an old account-feed source is no longer listed). The default "Overview" is one large row per subscription: avatar, name, new / not-downloaded counts and a strip of the latest covers. Click a cover to open the video's detail page, right-click it for details, play, download or mark; click the row or "View all" to see every video of that author / playlist as a poster grid like Search, with state filters, title search and bulk download with the chosen rule. Opening a subscription puts the covers of the visible page at the front of the download queue (no longer behind hundreds of covers from the overview), the status line shows "loading covers (n left)", and covers that failed are retried the next time you look or press Refresh. "Table" in the header keeps the original list / cover view, whose cover mode uses the same cover-size slider.

The Task Center now shows the total download speed of all running tasks at the top.

## Author pages, subscription state and Like

"Open author page" in a search result, Home card, detail page or Subscriptions page now opens the in-app author page by default: an author you subscribe to opens on their subscription grid, anyone else shows their works on Iwara (videos / images, with paging and bulk download). The author page and subscription grid carry two status chips: "Subscribed in this app / Not subscribed in this app" and "Followed on Iwara / Not followed on Iwara / not signed in" (the latter needs an Iwara sign-in), with buttons to subscribe or unsubscribe in the app and to follow or unfollow on the Iwara website. The detail page's author card has the same bar plus an "Author page" button. Oreno3D results with no resolvable Iwara account can still use "Open author page in browser".

The detail page also has a Like button: signed in, it likes a video or image post and a second click removes the like, with the count updating at once; signed out it asks you to sign in first.

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
