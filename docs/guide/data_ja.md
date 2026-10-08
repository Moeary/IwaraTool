# ローカルデータとキャッシュ

[English](./data_en.md) | [简体中文](./data.md) | [README に戻る](../../readme_ja.md)

実行時データは `data/` に保存され、用途ごとに分離されています。検索と購読のカバーは、ダウンロードルールによって生成されるローカルカバーとは混在しません。

| パス | 用途 |
| --- | --- |
| `data/config.ini` | Token、UI、並列数、ダウンロード動作の設定 |
| `data/tasks.json` | 復元可能な待機中、実行中、失敗、中断タスクの状態 |
| `data/history.db` | ダウンロード履歴、購読元、購読動画、更新状態 |
| `data/rules.json` | 名前付きダウンロードルール（フィルター、カバー、NFO 設定を含む） |
| `data/logs/` | ローテーションログ（`iwaratool.log`、1 MB × 最大 4 ファイル） |
| `data/updates/` | ワンクリック更新で取得した新版（検証後にのみ適用） |
| `data/x_version_salts.json` | X-Version ソルトを上書きする任意の JSON リスト |
| `data/backup_before_restore/` | バックアップ復元時に退避した旧ファイル |
| `data/iwara_tags.json` | 三言語フィールドを含む生成済みオフラインタグ索引 |
| `app/data/tag_translations/loveiwara_iwara_tags_localized.json` | アプリに同梱する LoveIwara MIT 翻訳ソース |
| `data/tag_translations/loveiwara_iwara_tags_localized.json` | 「タグを更新」で保存される実行時翻訳キャッシュ |
| `data/img/search/` | 検索ページ画像キャッシュ（Oreno3D/Iwara） |
| `data/img/sub/` | 購読ページ動画カバーキャッシュ。履歴にある Iwara カバーを再利用可能 |
| `data/img/avatar/` | 購読作者アバターキャッシュ。旧形式の `avatar_*` ファイルは起動時に移行して再利用 |

パッケージ版は `app/data/` の LoveIwara 辞書を同梱し、実行ファイル隣の `data/tag_translations/` に実行時キャッシュが無い場合、初回起動時に自動展開します。既存の実行時キャッシュは保持されます。開発環境の `data/` にあるその他のローカル状態は自動的に埋め込まれないため、更新時は既存の `data/` を残してください。

ダウンロードルールで NFO 生成が有効な場合、NFO は動画と同じベース名で動画の隣に書き込まれます。新規ファイルにはメディアセンター取り込み用の標準的な映画メタデータ項目が含まれ、Iwara 固有のエイリアスも保持されます。既存の NFO ファイルは自動的に書き換えられません。

検索ページの Oreno3D 結果はオンラインで取得され、現在の結果の画像のみをキャッシュします。
