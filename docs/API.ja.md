# IwaraTool API ノート（日本語）

[English](./API.md) | [简体中文](./API.zh.md)

このドキュメントは、現在このプロジェクトで実装済みの API 機能をまとめたものです。

## 1. 認証

### ログイン
- エンドポイント：`POST https://api.iwara.tv/user/login`
- ボディ：
  - `email`：ユーザー名またはメール
  - `password`：アカウントパスワード
- 成功時：
  - `token`（Bearer Token）を返す

### Token の利用
- 認証ヘッダー：
  - `Authorization: Bearer <token>`
- Token は `data/config.ini` に保存：
  - `auth_token`
  - `auth_token_saved_at`
- 起動時の挙動：
  - まず保存済み token を優先利用（高速）
  - token が無い場合のみユーザー名/パスワードへフォールバック

## 2. 動画情報とダウンロード URL 解決

### 動画メタ情報取得
- エンドポイント：`GET https://api.iwara.tv/video/{video_id}`

### ダウンロードソース解決
- 入力：動画メタ情報の `fileUrl`
- `X-Version` ヘッダーを追加（計算元）：
  - `{filename}_{expires}_{salt}`
  - SHA1
- 画質フォールバック：
  - `Source -> 540 -> 360`

## 3. 一括取得元

### ユーザー単位
- ユーザー id 解決：
  - `GET https://api.iwara.tv/profile/{username}`
- 動画一覧取得：
  - `GET https://api.iwara.tv/videos?user={user_id}&sort=date&page={n}`

### プレイリスト単位
- `GET https://api.iwara.tv/playlist/{playlist_id}?page={n}`

## 4. 検索ダウンロード（新規）

ダウンロード入力欄に検索 URL を直接貼り付けて使えます。

### 対応フォーマット
- `https://api.iwara.tv/videos?...`
- `https://www.iwara.tv/videos?...`
- 例：
  - `https://api.iwara.tv/videos?tags=2d&sort=date`
  - `https://www.iwara.tv/videos?tags=2d&sort=date`

### 挙動
- URL クエリを解析
- `GET /videos` をページング取得
- 返却された動画 ID を自動でキュー投入

### 検索専用の件数上限
- 設定項目：`Search Download Limit`
- 設定キー：
  - `search_limit_enabled`（bool）
  - `search_limit_count`（int）
- 有効時、先頭 `N` 件のみをキュー投入
- URL に `limit` がある場合、最終上限は `min(url limit, setting limit)`

## 5. フィルター

グローバルフィルターは解決ステージで適用されます。

### 対応項目
- 最小いいね
- 最小再生数
- 公開日レンジ
- 含めるタグ（include）
- 除外タグ（exclude）

### タグ一致ルール
- 大文字小文字を区別しない
- 区切り文字：
  - `,`、`，`、空白、`;`、`|`
- 正規化して比較するフィールド：
  - `id`、`type`、`slug`、`name`、`title`

## 6. UI が受け付ける URL 種別

- 単体動画：
  - `https://www.iwara.tv/video/{id}`
- ユーザー：
  - `https://www.iwara.tv/profile/{name}` または `/user/{name}`
- プレイリスト：
  - `https://www.iwara.tv/playlist/{id}`
- 検索 URL：
  - `https://api.iwara.tv/videos?...`
  - `https://www.iwara.tv/videos?...`

## 7. タグ収集スクリプト（将来のワンクリックタグ絞り込み準備）

- スクリプトパス：
  - `app/core/crawl_iwara_tags.py`
- 目的：
  - `/tags` エンドポイントを `A-Z` と `0-9` で巡回してタグ収集
  - 三言語拡張可能な JSON + Markdown を出力
- エンドポイント形式：
  - `https://apiq.iwara.tv/tags?filter={A-Z0-9}&page={n}`
- 既定の出力：
  - `data/iwara_tags.json`
  - `docs/iwara_tags.md`
- 翻訳フィールド：
  - 各タグに `name_en`、`name_zh`、`name_ja` を生成
  - 既定値は元タグ文字列にフォールバック
  - `--translation-map path/to/map.json` で上書き可能
- 例：
  - `pixi run python app/core/crawl_iwara_tags.py`
