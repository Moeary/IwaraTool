# IwaraTool（日本語）

[English](./readme.md) | [简体中文](./readme.zh.md)

Python + PySide6 で作られた Iwara 向けダウンローダーです。大量ダウンロードに向いています。

![](https://raw.githubusercontent.com/Moeary/pic_bed/main/img/202603051905789.png)

## 主な機能
- `X-Version` 署名計算に対応。
- 画質フォールバック：`Source -> 540 -> 360`。
- ステートマシン制御で URL 失効問題を軽減。
- ローカル重複回避 + SQLite 履歴保存。
- ユーザー、プレイリスト、検索 URL の一括投入に対応。
- フィルター：いいね、再生数、日付範囲、タグ include/exclude。
- 検索ダウンロード件数上限を個別設定可能。
- `data/config.ini` に token を保存し起動ログインを高速化。
- 中/英/日のリアルタイム切替（再起動不要）。
- ファイル名テンプレートは複数プレースホルダーと別名に対応。
- aria2 RPC、サムネイル保存、`.nfo` 生成は任意で有効化可能。

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

## ドキュメント
- Wiki: <https://github.com/Moeary/IwaraTool/wiki>
- API（EN）：[docs/API.md](./docs/API.md)
- API（ZH）：[docs/API.zh.md](./docs/API.zh.md)
- API（JA）：[docs/API.ja.md](./docs/API.ja.md)
- タグ索引：[docs/iwara_tags.md](./docs/iwara_tags.md)

## タグ収集
```bash
pixi run python app/core/crawl_iwara_tags.py
```

## 実行 / ビルド
```bash
pixi run start
pixi run crawl
pixi run build
```

## ライセンス
MIT License。
