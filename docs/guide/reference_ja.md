# リファレンス：プラットフォーム、URL、内部動作、ビルド

[English](./reference_en.md) | [简体中文](./reference.md) | [README に戻る](../../readme_ja.md)

多くのユーザーには不要な詳細をまとめています。README には使い始めに必要な内容だけを残しています。

## プラットフォームの注意

- **Windows**：[Releases](https://github.com/Moeary/IwaraTool/releases) から `.exe` を取得して実行します。ログイン時の自動起動とワンクリック更新（SHA-256 検証付き）は Windows のみ対応しています。
- **Linux**：バイナリは GitHub の `ubuntu-latest` ランナーでビルドされるため、古い glibc の環境はサポート対象外です。古いディストリビューションではソースを取得してローカルでビルドしてください。
- **macOS**：Apple Silicon（ARM64）と Intel（AMD64）の ZIP を提供します。解凍後、`IwaraTool.app` を「アプリケーション」に移動してください。CI は macOS 15 で検証し、旧バージョンは未検証です。Developer ID 署名・公証はありません。起動がブロックされた場合は「プライバシーとセキュリティ」の案内に従って許可してください。
  - データは `~/Library/Application Support/IwaraTool/`、既定の保存先は `~/Downloads/IwaraTool/` です。
  - 更新時は `.app` を置き換えてください。データは保持されます。

## 対応 URL

```text
https://www.iwara.tv/profile/username
https://www.iwara.tv/profile/username/videos
https://www.iwara.tv/playlist/xxxxxxxx
https://www.iwara.tv/video/xxxxxxxx
https://www.iwara.tv/videos?sort=date
https://www.iwara.tv/videos?tags=2d&sort=likes
https://api.iwara.tv/videos?tags=2d&sort=date
```

`sort`：`date`、`trending`、`popularity`、`views`、`likes`。

`tags` の詳細は [タグ索引](../iwara_tags.md) を参照してください。

## 命名テンプレート

命名テンプレートは、各動画をダウンロードフォルダー内のどこに保存するかを決めます。ルール編集画面ではプリセットを選ぶか、フィールドを順に選んでその間の文字（`_`、`-`、スペース、または `/` で次のフォルダー階層）を入力して組み立てられます。完全なテンプレートも直接編集でき、リアルタイムでプレビューされます。

| プレースホルダー | 意味 |
| --- | --- |
| `{author}` | 作者アカウントID（例：`user154126`） |
| `{username}` | 作者ニックネーム |
| `{title}` / `{id}` / `{slug}` | 動画タイトル / ID / URLスラッグ |
| `{YYYY-MM-DD}` `{date}` / `{YYYY}` / `{MM}` / `{DD}` | 公開日とその年・月・日 |
| `{quality}` | 保存した画質 |
| `{views}` `{likes}` `{comments}` | 再生数・いいね数・コメント数 |
| `{duration}` | 長さ（秒） |
| `{rating}` | レーティング |

末尾には自動で `.mp4` が付きます。Windows の予約名、長すぎるパス、使えない文字は自動で修正されます。

## 内部動作

- **ダウンロードエンジン**：`X-Version` 署名計算、画質フォールバック（`Source -> 540 -> 360`）、ステートマシン制御による URL 失効の軽減。任意で aria2 RPC。
- **信頼性**：ダウンロードのサイズ・レジューム検証、API リクエストのバックオフ再試行と間隔制御、SQLite WAL、ローテーションログ（`data/logs/`）。
- **永続化**：タスクキューは安全終了後に自動復元され、履歴と購読は SQLite に保存されます。[ローカルデータとキャッシュ](./data_ja.md) を参照してください。
- **検索**：Iwara ライブ API と Oreno3D ブリッジ。タグ候補は同梱の英中日辞書から提供されます。
- **API メモ**：[API_ja.md](../API_ja.md)。

## ソースから実行 / ビルド

依存関係は [pixi](https://pixi.prefix.dev/latest/) で管理されています。

```shell
pixi install
pixi run start  # プログラムを実行
pixi run build  # アプリをビルド
pixi run crawl  # タグデータを取得（docs/iwara_tags.md を更新）
pixi run test   # テストを実行
```

Windows ビルドにはバージョン情報が埋め込まれます。環境変数 `WINDOWS_SIGN_PFX`（.pfx のパス。CI では `WINDOWS_SIGN_PFX_BASE64`）と `WINDOWS_SIGN_PFX_PASSWORD` を設定すると、`pixi run build` がタイムスタンプ付きの Authenticode 署名も行います（Windows SDK があれば signtool、なければ PowerShell）。既存ファイルのみ署名するには `python scripts/build_windows.py --sign-only <exe>` を使います。
