# リファレンス：プラットフォーム、URL、内部動作、ビルド

[English](./reference_en.md) | [简体中文](./reference.md) | [README に戻る](../readme/readme_ja.md)

多くのユーザーには不要な詳細をまとめています。README には使い始めに必要な内容だけを残しています。

## プラットフォームの注意

- **Windows**：[Releases](https://github.com/Moeary/IwaraTool/releases) から `IwaraTool-Windows-AMD64-<バージョン>.zip` を取得し、展開して `IwaraTool.exe` を実行します。データはプログラム横の `data/` に保存されるため、旧バージョンのフォルダーに展開すればそのデータを引き継げます。トレイアイコンとログイン時の自動起動に対応しています。
- **macOS**：Apple シリコン（ARM64）向けの ZIP に `IwaraTool.app` が入っています。「アプリケーション」にドラッグしてください。CI は macOS 15 で検証し、旧バージョンは未検証です。Developer ID 署名・公証はありません。初回起動時のブロックへの対処とプロキシ設定は [インストールと基本の使い方](./install_ja.md) を参照してください。
  - データは `~/Library/Application Support/IwaraTool/`、既定の保存先は `~/Downloads/IwaraTool/` です。
  - 更新時は `.app` を置き換えてください。データは保持されます。
- **Android**：`IwaraTool-Android-<バージョン>.apk`。すべてのリリースを同じ長期署名鍵で署名しているため、新しい APK を上書きインストールしてそのまま更新でき、データも残ります。
- **Linux / macOS Intel**：ビルド済みパッケージは現在ありません。ソースから実行してください（下記）。

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
pixi run start        # プログラムを実行
pixi run demo         # オフラインデモ：架空のデータで、実データには触れません
pixi run test         # テストを実行
pixi run build        # このシステムのデスクトップ版を dist/ にパッケージ（macOS は --install でテスト版をインストール）
pixi run apk          # Android APK をビルド（Android SDK が必要）
pixi run screenshots  # デモデータで各ウィンドウサイズのスクリーンショット
pixi run i18n         # UI テキストを集計して翻訳を確認
pixi run crawl        # タグデータを取得（docs/iwara_tags.md を更新）
pixi task list        # すべてのタスクを表示
```

`pixi run build` は `flet build` でこのシステム向けのデスクトップ版をパッケージします（`--platform apk` は Android。Android SDK が必要で、通常は CI でビルドします）。Android のリリースは 1 つの長期署名鍵で署名します。`FLET_ANDROID_SIGNING_KEY_STORE`（.jks のパス）、`FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD`、`FLET_ANDROID_SIGNING_KEY_ALIAS` を設定してビルドすると、スクリプトが APK の証明書を `scripts/tools/build.py` の `RELEASE_CERT_SHA256` と照合します。CI はリポジトリの secrets `ANDROID_KEYSTORE_BASE64`・`ANDROID_KEYSTORE_PASSWORD`・`ANDROID_KEY_ALIAS` を使い、タグでのリリース時に鍵がなければ失敗します。