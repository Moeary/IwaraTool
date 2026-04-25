# IwaraTool

![logo](./docs/iwaratool_logo.png)

[English](./readme.md) | [简体中文](./readme_zh.md)

煩わしいコマンドラインにさよなら！モダンな Fluent スタイルのインターフェースを備えた Iwara 一括ダウンローダー。初心者でもワンクリックで作者の全動画をダウンロードできます。

![demo](./docs/iwaratool_demo.gif)

## 主な機能
- `X-Version` 署名計算に対応。
- 画質フォールバック：`Source -> 540 -> 360`。
- ステートマシン制御で URL 失効問題を軽減。
- ローカル重複回避 + SQLite 履歴センター。
- ユーザー、プレイリスト、検索 URL の一括投入に対応。
- フィルター：いいね、再生数、日付範囲、タグ include/exclude。
- 検索ダウンロード件数上限を個別設定可能。
- `data/config.ini` に token を保存し起動ログインを高速化。
- 中/英/日のリアルタイム切替（再起動不要）。
- ファイル名テンプレートは複数プレースホルダーとディレクトリ制御に対応。
- aria2 RPC、サムネイル保存、`.nfo` 生成は任意で有効化可能。
- 履歴センターは検索、フィルター、ソート、ファイルを開く、名前変更、移動済み履歴の削除に対応。
- 再試行前に対応する一時キャッシュを削除し、壊れたキャッシュによる再失敗を軽減。

## クイックスタート
1. [Releases](https://github.com/Moeary/IwaraTool/releases) から最新版を取得。
2. 起動後、先にログイン。
3. `新規ダウンロード` に URL を貼り付けてキュー投入。

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

`tags` の詳細は [タグ索引](./docs/iwara_tags.md) を参照してください。

## ドキュメント
- Wiki: <https://github.com/Moeary/IwaraTool/wiki>
- API（JA）：[docs/API_ja.md](./docs/API_ja.md)
- タグ索引：[docs/iwara_tags.md](./docs/iwara_tags.md)

## 実行 / ビルド

プロジェクトの依存関係は [pixi](https://pixi.prefix.dev/latest/) で管理されています。

開発者としてソースコードを直接実行したい、またはアプリをビルドしたい場合：

```shell
pixi run start // プログラムを実行
pixi run build // アプリをビルド
pixi run crawl // タグデータを取得（docs/iwara_tags.md を更新）
```

## 貢献

PRは大歓迎です！
現在、プログラムの i18n（多言語）はまだ不十分であり、ダーク/ライトモードの切り替えもまだ実装されていません。これらはコアなダウンロード機能には影響しませんが、もし時間があり、改善に協力していただける場合は、直接 PR を提出してください（PRを提出する際は、標準的な更新を遵守して dev ブランチにマージし、提出前に GitHub Action の CI/CD を通過させるようにしてください）。ありがとうございます！

## ライセンス

MIT License。

**本プログラムをダウンロードした時点で、MIT ライセンスを遵守することに同意したものとみなされます。詳細は LICENSE ファイルを参照してください。**

1. 本プロジェクトを違法、公序良俗に反する目的、または法令に違反する目的で使用することを禁止します。違反した使用により生じた損害については、ユーザーが全責任を負うものとします。
2. 本プロジェクトで提供されるパッケージ版およびスクリプトは、個人の学習および研究目的のみに使用されるものであり、許可なく商用での再配布や転売を行うことはできません。
3. プロジェクトの管理者は、法令またはコミュニティのフィードバックに基づき、いつでもサービスおよびサポートを更新、中断、または終了する権利を留保します。

## 特別感謝

[hare1039](https://github.com/hare1039) 氏の [iwara-dl](https://github.com/hare1039/iwara-dl/tree/master) プロジェクトから貴重な参考とインスピレーションをいただきました。特に X-Version 署名計算とダウンロードリンク解析の実装詳細は、本プロジェクトの開発に大きな推進力となりました。
