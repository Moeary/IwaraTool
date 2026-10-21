# IwaraTool

![logo](./docs/iwaratool_logo.png)

[简体中文](./readme_zh.md) | [日本語](./readme_ja.md)

Say goodbye to tedious command-line tools! Iwara batches downloader with a modern Fluent-style interface, making it easy for anyone to download all videos from a creator with one click.

![demo](./docs/iwaratool_demo_v0.6.gif)

## What you can do

- **Download in bulk**: paste a creator, playlist, search or video link and download everything in one go. Unfinished tasks pick up again the next time you open the app.
- **Search and browse**: search Iwara and Oreno3D, get tag suggestions in Chinese, English and Japanese, and build a Home page of your own rows (your subscriptions, hot lists, any tag or keyword). Videos you already have are marked.
- **Follow creators**: subscribe to authors and playlists, see new videos at a glance, and download only what's new.
- **Preview before you download**: double-click any video to play it, right inside the app.
- **Download only what you want**: save rules filter by likes, views, date, tags and title keywords, and each page can use its own rule.
- **Name files your way**: pick a naming preset, or build one by choosing fields (author, title, ID, date, quality…) and what goes between them, with a live preview.
- **Tidy existing files**: the Repair Center renames or reorganizes videos you already downloaded.
- **Keep track**: download history with search and filters, duplicate detection, optional cover images and `.nfo` files for media libraries.
- **Made to live with**: Chinese / English / Japanese, light and dark themes, system tray, launch at login, backup and one-click update on Windows.

## Screenshots

All screenshots use the English interface with sample data and no cover images.

1. Download Workbench

   ![Download Workbench](./docs/panel_view/iwaratool_download_panel.jpg)
2. Search

   ![Search](./docs/panel_view/iwaratool_search_panel.jpg)
3. Subscriptions

   ![Subscriptions](./docs/panel_view/iwaratool_subscription_panel.jpg)
4. History

   ![History](./docs/panel_view/iwaratool_history_panel.jpg)
5. Rules (including the rule per page)

   ![Rules](./docs/panel_view/iwaratool_rule_panel.jpg)
6. Settings

   ![Settings](./docs/panel_view/iwaratool_settings_panel.jpg)

## Quick Start

1. Download the latest version from [Releases](https://github.com/Moeary/IwaraTool/releases) and run it. (Linux and macOS notes: see [Reference](./docs/guide/reference_en.md#platform-notes).)
2. Sign in if you want private videos or the authors you follow.
3. Paste a link in `Download Workbench`, pick a rule if needed, and submit.
4. Use `Subscriptions` to follow authors or playlists and download what's new.

More: [Usage Guide](./docs/guide/usage_en.md) · [Reference (supported links, naming placeholders, build from source)](./docs/guide/reference_en.md) · [Local Data and Caches](./docs/guide/data_en.md) · [Wiki](https://github.com/Moeary/IwaraTool/wiki)

## Contributing

Pull Requests are very welcome!
Please target the `dev` branch, keep English/Simplified Chinese/Japanese UI text in sync, and run the test suite before submitting (build and test commands: [Reference](./docs/guide/reference_en.md#run--build-from-source)). UI changes should include a screenshot or short recording when practical.

## Code signing policy

Windows releases are built from this repository by GitHub Actions ([build.yml](.github/workflows/build.yml)) and signed only after the release is approved.

Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).

- Committers and reviewers: [Moeary](https://github.com/Moeary)
- Approvers: [Moeary](https://github.com/Moeary)

Every change from an outside contributor is reviewed before it is merged, and every signing request is approved by hand.

### Privacy policy

This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it. It talks only to the services needed for what the user asks:

- iwara.tv (`www`, `api`, `apiq`, `i` and the file servers it returns) to browse, search, sign in and download; the account and password are sent only to Iwara when the user signs in.
- oreno3d.com, when the user searches through the Oreno3D bridge.
- GitHub (`api.github.com`, `raw.githubusercontent.com`) to check for a new release and to fetch the tag dictionary; the release check can be turned off in Settings.
- A proxy or a local aria2 RPC server, only when the user configures one.

Settings, history and caches stay in the `data` folder next to the program.

## License

MIT License.

**By downloading this program, you agree to comply with the MIT License. See the LICENSE file for details.**

1. Using this project for any illegal, regulatory-violating purpose or any purpose against public order and good morals is prohibited; the user bears full responsibility for losses caused by non-compliant use.
2. The packaged versions and scripts provided by the project are for personal learning and research only, and may not be used for commercial redistribution or resale without permission.
3. Project maintainers reserve the right to update, suspend, or terminate services and support at any time in accordance with laws and regulations or community feedback.

## Special Thanks

Thanks to [hare1039](https://github.com/hare1039)'s [iwara-dl](https://github.com/hare1039/iwara-dl/tree/master) project for its valuable reference and inspiration, especially in the implementation details of X-Version signature calculation and download link parsing, which played a key role in the development of this project.
