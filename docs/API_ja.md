# IwaraTool API ノート（日本語）

[English](./API.md) | [简体中文](./API_zh.md)

このドキュメントは、IwaraTool が使う Iwara REST API と、どの機能がどのリクエストを送るかをまとめたものです。Iwara に公式の公開 API はありません。以下の内容は、稼働中のサイトでの実測、本プロジェクトのテスト、オープンソースのクライアント [LoveIwara](https://github.com/FoxSensei001/LoveIwara) と [gallery-dl](https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/iwara.py) の実装に基づきます。各エンドポイントには **検証済み**（本プロジェクトが実 API で確認）または **参考**（他クライアントのソースに基づき、実アカウントでは未確認）を付けています。

目次：[1 共通事項と認証](#1-共通事項と認証) · [2 取得系エンドポイント](#2-取得系エンドポイント) · [3 アカウント操作](#3-アカウント操作) · [4 機能とリクエストの対応](#4-機能とリクエストの対応) · [5 検索ダウンロード](#5-検索ダウンロード) · [6 Fluent 検索ページ](#6-fluent-検索ページ) · [7 NFO 生成とメディアセンター互換](#7-nfo-生成とメディアセンター互換) · [8 フィルター](#8-フィルター) · [9 UI が受け付ける URL 種別](#9-ui-が受け付ける-url-種別) · [10 タグ収集スクリプト](#10-タグ収集スクリプトとローカル翻訳キャッシュ)

## 1. 共通事項と認証

### ホストとヘッダー
- API ホスト：`https://api.iwara.tv`。`https://apiq.iwara.tv` も同じルートに応答し、動画詳細・フォロー一覧・タグ収集のフォールバックに使います。
- メディアホスト：`https://i.iwara.tv`（カバー、アバター、画像投稿の画像）。
- すべてのリクエストに `Accept: application/json, text/plain, */*`、`Origin: https://www.iwara.tv`、`Referer: https://www.iwara.tv/`、`X-Site: www.iwara.tv` を付けます。HTTP セッションは `cloudscraper`（Chrome / Windows プロファイル）で、Cloudflare のブラウザー確認を通します。
- エラーは JSON `{"message": "errors.xxx"}` で返ります（例：`errors.privateVideo`、`errors.notFound`、`errors.badRequest`）。クライアントは HTTP／ネットワーク失敗を読みやすいメッセージにし、失敗を空の結果として扱いません。

### 間隔とリトライ
- 同じホストへのリクエストは少なくとも `request_min_interval_ms`（既定 200 ms）空けます。
- `429`・`502`・`503`・`504` と一時的なネットワークエラーは、最大 `request_max_retries` 回（既定 2）、指数バックオフ（1秒・2秒・4秒…最大30秒）で再試行します。`Retry-After` は最大30秒まで尊重します。
- 設定は「設定 → システムと保守 → リクエスト間隔」にあります。バックグラウンド処理（ホームの欄・カバー・購読更新）はワーカーごとに独立した HTTP セッションを使い、同じトークンとプロキシを引き継ぐため、独立した要求は順番待ちせず並行して走ります。

### ログイン — 検証済み
- `POST /user/login`、ボディ `{"email": "<ユーザー名またはメール>", "password": "<パスワード>"}`。成功時 `{"token": "<jwt>"}` を返します。
- 認証が必要なリクエストはすべて `Authorization: Bearer <token>` を付けます。
- トークンは `data/config.ini`（`auth_token`、`auth_token_saved_at`）に保存。起動時は保存済みトークンを優先し、トークンが無いときだけユーザー名/パスワードでログインします。
- 公開コンテンツはログイン不要です。ログインすると非公開動画、購読フィード、以下のアカウント項目（`liked`、`following`）が使えます。

## 2. 取得系エンドポイント

### 動画・画像の詳細 — 検証済み
- `GET /video/{id}`：動画 1 件。アプリが読む項目は `id`、`title`、`body`、`rating`（`general` | `ecchi`）、`numLikes`、`numViews`、`numComments`、`createdAt`、`user`（`id`、`name`、`username`、`avatar`）、`tags[]`（`id`、`type`）、`file`（`id`、`duration`、`height`）、`fileUrl`、`thumbnail`（カバー番号）、`embedUrl`（YouTube 埋め込みは保存不可）、ログイン時は `liked`。
- `GET /image/{id}`：画像投稿 1 件。上記に加えて `numImages` と `files[]`（`id`、`name`、`width`、`height`）。画像は `https://i.iwara.tv/image/original/{file.id}/{file.name}` と `https://i.iwara.tv/image/thumbnail/{file.id}/{file.id}.jpg` から配信されます。
- カバー：`https://{host}/image/original/{file.id}/thumbnail-{NN}.jpg`。ホームのカードは `original` を `thumbnail`（約 10 KB の小さい版）に置き換えます。アバター：`https://i.iwara.tv/image/avatar/{id}/{name}`。
- `GET /video/{id}/related?limit=12`、`GET /image/{id}/related?limit=12`：関連投稿（SFW/NSFW の選択を無視するため、アプリ側で絞り込みます）。
- `GET /video/{id}/comments?page=0&limit=20`、`GET /image/{id}/comments?...`：コメント。`&parent={コメントID}` でそのコメントへの返信を取得。応答は `{count, results[]}`。

### ダウンロードソース — 検証済み
- 入力：動画詳細の `fileUrl`。`Authorization`（ログイン時）と `X-Version` ヘッダーを付けて `GET` すると、`{name, src: {view, download}}` の一覧が返ります。
- `X-Version = sha1("{ファイルuuid}_{expires}_{salt}")`。uuid は `/file/` の後のパス区間、`expires` は URL のクエリ文字列から取ります。サイトは時々 salt を更新するため、内蔵 salt とユーザーが追加した salt（設定、または `data/x_version_salts.json`）を順に試します。
- 画質フォールバック：希望画質から `Source → 540 → 360`。`fileUrl` が無い場合は、非公開動画（`errors.privateVideo`）、外部埋め込み、または利用不可の投稿です。

### 一覧：`/videos` と `/images` — 検証済み
`GET /videos` と `GET /images` は同じパラメーターを取ります。

| パラメーター | 意味 |
|---|---|
| `page`、`limit` | 0 始まりのページと件数（アプリは 1–100 を送信。ホームは 24、検索は 32、「もっと見る」一覧は 32）。 |
| `sort` | `date`、`trending`、`popularity`、`views`、`likes`。 |
| `rating` | `general`（SFW）または `ecchi`（NSFW）。省略すると両方。 |
| `tags` | カンマ区切りのタグ ID。すべて一致が必要。**複数形**：実 API は単数形 `tag` を無視します（アプリは古い入力を `tags` に変換）。`/images` では使えません。 |
| `user` | ユーザー ID（ユーザー名ではない）。`GET /profile/{username}` で解決します。 |
| `subscribed=true` | ログイン中アカウントの購読フィード（トークンが必要）。 |

応答：`{count, limit, page, results[]}`。`count` は安定した総数では**ありません**。満杯のページでは `(page + 1) × limit + 1` となり、アプリはこれをページ数ではなく「次のページがある」印として扱います。存在しないタグは HTTP 500 を返すことがあるため、サーバーエラーを「該当なし」とは見なしません。

### テキスト検索：`/search` — 検証済み
`GET /search?type={videos|images|users|playlists}&query=…&sort=…&page=…&limit=…`

- 種別ごとの並び順：`videos`・`images` は `relevance`、`date`、`views`、`likes`。`users`・`playlists` は `relevance` と `date` のみ（`views`／`likes` は `errors.badRequest` になるため、クライアントは関連度に戻します）。
- このエンドポイントは `rating` を無視するので、SFW／NSFW は結果をローカルで絞り込みます。
- 空の `query` は送りません。動画の閲覧は `/videos`、画像の閲覧は `/images` を使います。

### ユーザー — 検証済み
- `GET /profile/{username}` → `{user: {id, name, username, avatar, …}}`。ログイン時は `user` に `following`（自分がフォロー中か）、`followedBy`、`friend` も含まれます。アプリの「Iwaraでフォロー中」表示の元です。
- `GET /user/{id}/following?page=…&limit=50`：そのユーザーがフォローしているアカウント（「フォロー作者を取込」で使用）。

### プレイリスト — 検証済み
- `GET /playlist/{id}?page={n}`：プレイリストの動画。プレイリストのカードは `/search?type=playlists` から来ます。

### タグ — 検証済み
- `GET https://apiq.iwara.tv/tags?filter={A-Z0-9}&page={n}`：タグ一覧（第 10 節）。

## 3. アカウント操作

次の 3 種類はトークンが必要で、未ログイン時にクライアントは送信せず「先にログインしてください」と表示します。`2xx` の応答はすべて成功扱いで、本文は使いません。**参考**（LoveIwara クライアントに基づく。本プロジェクトのテストはモックセッションで、実アカウントは使っていません）。

| 操作 | リクエスト |
|---|---|
| 動画／画像にいいね | `POST /video/{id}/like` · `POST /image/{id}/like` |
| いいねを取り消す | `DELETE /video/{id}/like` · `DELETE /image/{id}/like` |
| サイト上で作者をフォロー | `POST /user/{userId}/followers` |
| フォロー解除 | `DELETE /user/{userId}/followers` |

- 詳細ページに表示するいいね状態は `GET /video/{id}` ／ `/image/{id}` の `liked` です。クリック成功後、件数はローカルですぐ変わります。
- サイト上のフォローは、アプリの**ローカル購読**（`data/subscriptions.db` の 1 行）とは別物です。作者ページは両方の状態を並べて表示し、それぞれ別に変更できます。「フォロー作者を取込」はサイトのフォローをローカル購読にコピーします。

## 4. 機能とリクエストの対応

| 機能 | リクエスト |
|---|---|
| ホーム —「購読中の更新」 | `/videos?subscribed=true&sort=date[&rating]` と `/images?…`（要ログイン） |
| ホーム — 人気の欄 | `/videos?sort=trending\|popularity\|date[&rating]`、`/images?…` |
| ホーム — タグの欄 | `/videos?tags=<解決済みID>&sort=…[&rating]`（名前は先にローカルのタグ辞書を通す） |
| ホーム — キーワードの欄 | `/search?type=videos\|images&query=…&sort=…` |
| ホーム — 作者の欄・作者ページ | セッションごとに `/profile/{username}` で ID を 1 回取得し、`/videos?user={id}&sort=date` ／ `/images?…` |
| ホーム — 件数とキャッシュ | 各欄 24 件（0 ページ目）。欄・タブ・コンテンツ区分ごとに `data/home_feed_cache.json` へ保存。キャッシュの「署名」は投稿 ID の並びなので、再確認で同じ ID が返ればカードには触れません。再確認までの時間：設定 → ホーム（既定 15 分、0 は手動のみ）。 |
|「検索ページで見る」| 対応する検索ページのクエリ（種類・タグ／キーワード・並び順）。作者の欄はアプリ内の作者ページを開く |
| 投稿の詳細 | `/video/{id}` または `/image/{id}`、続いて `/related`、`/comments`。いいね → `POST|DELETE …/like` |
| 作者ページ／状態バー | `/profile/{username}`（フォロー状態）＋ `/videos?user=…`。フォロー → `POST|DELETE /user/{id}/followers` |
| 購読の更新 | `/videos?user={id}&sort=date`（増分：最初の既知 ID で停止）、`/playlist/{id}`、`/videos?subscribed=true`。一覧の行にファイル ID が無いときは `/video/{id}` でカバーを補完 |
| 検索ページ | `/videos`、`/images`、`/search`、`/profile/{username}`、`/playlist/{id}`（第 6 節） |
| ダウンロード | `/video/{id}` → `fileUrl` → ソース（第 2 節） |

## 5. 検索ダウンロード

ダウンロード入力欄に API 検索 URL を直接貼り付けて使えます。

### 対応フォーマット
- `https://api.iwara.tv/videos?...`
- `https://www.iwara.tv/videos?...`
- 例：`https://api.iwara.tv/videos?tags=2d&sort=date`

Web ルートも JSON API も複数形の `tags` を使います（2026-10-03 に確認：単数形の `tag` は無視され、絞り込まれない結果が返ります）。IwaraTool は古い単数形も受け付け、API 境界で `tags` に正規化します。

### 挙動
- URL クエリを解析
- `GET /videos` をページング取得
- 返却された動画 ID を自動でキュー投入

### 検索専用の件数上限
- 設定項目：`Search Download Limit`。設定キー：`search_limit_enabled`（bool）、`search_limit_count`（int）
- 有効時、先頭 `N` 件のみをキュー投入
- URL に `limit` がある場合、最終上限は `min(url limit, setting limit)`

## 6. Fluent 検索ページ

サイドバーの「検索」ページは、オンラインで閲覧してまとめてキューに入れるための画面で、ダウンロードハブの「検索 URL をキューへ」とは別のものです。既定のソースは Oreno3D オンライン検索で、Iwara ライブ API に切り替えられます。どちらのサイトもローカルにミラーしません。

### 検索の種類
- **キーワード** — Iwara で空でないキーワード：`GET /search?type=videos&query=…&sort=…&page=0`。引用符付きの語句はそのまま送ります。空欄なら `/videos` を閲覧。Oreno3D：`GET https://oreno3d.com/search?keyword=…&sort=latest&page=1`。結果を開くときに Iwara ページへ解決します。
- **作者** — Iwara ライブ API でユーザー名から作者ページを取得し、紹介文と動画数を表示。自由なテキストは `/search?type=users` を使います。
- **タグ** — Iwara は `/videos?tags=…` を使い、複数タグはカンマで連結してすべて一致が必須。中日英の正式名は辞書でタグ ID に対応付けます。1 つの名前に複数の ID がある場合は盲目的に選ばず、明示的な選択を求めます。辞書に無い生の ID はそのまま保持します。Oreno3D は独自のタグ対応を使い、不明な名前はキーワード入口に戻ります。
- **画像** — `/images`、キーワードがあれば `/search?type=images`。キーワード空欄でも閲覧できます。
- **プレイリスト** — `/search?type=playlists`、またはプレイリスト ID／`/playlist/{id}` リンクで動画一覧を表示。
- ソースと種類ごとに入力と並び順の下書きを別々に保持するので、切り替えてもタイトルのキーワードをタグとして送ることはありません。履歴は元の入力とその種類を記録します。
- **作者ページを開く**（右クリックメニュー）は、Iwara アカウントを特定できるときアプリ内の作者ページを開きます（アプリでの購読状態と Iwara でのフォロー状態を表示。第 3 節）。特定できなければ元サイトの作者ページに戻ります。「作者ページをブラウザーで開く」は常に元サイトを開きます。

### 並び順とダウンロードルール
- Iwara のキーワード検索は `date`、`relevance`、`views`、`likes`。動画一覧とタグは `date`、`trending`、`popularity`、`views`、`likes`。選択欄はモードに合わせて変わります。
- Iwara の結果はサーバーの順序を保ちます。1 ページ内の並べ替えや、説明文の一致など有効な結果を落とすローカルの部分一致フィルターは行いません。「次へ」は同じクエリと並び順を引き継ぎ、未確定の入力はページ送り時に 1 ページ目から始めます。
- `/videos` の `count` は「ページ位置＋ページ容量＋1」という次ページの印のことがあり、`/search` の実際の総数は別に扱います。サーバーが別のページ容量を使う場合、確認できる次ページ操作だけを残します。HTTP エラーや不正な結果構造は理由を表示し、0 件の結果に見せかけません。
- 検索ページはダウンロードハブの詳細フィルターを複製せず、ページ内の「ダウンロードルール」選択で既存のルールを使います。ルールを選ぶと、メタデータのフィルター・命名テンプレート・カバー／NFO・ダウンロード動作がすぐ適用され、既定ルールにもなります。
- SFW／NSFW のとき、`/search` は 100 件ずつ取得してローカルで絞り込み、約 24 件に満たなければ後続ページ（最大 4 ページ）を読み込みます。

### Iwara 検索の検証（2026-10-03）
プロジェクトの匿名 `IwaraAPI` セッションによる読み取り専用リクエスト。メディアは保存していません。
- `/videos?q=zz_iwaratool_no_such_query_13` は絞り込みなしの一覧と同じ 4 件の ID を返し、`/search?type=videos&query=zz_iwaratool_no_such_query_13` は 0 件でした。旧キーワードパラメーターは絞り込みなしの一覧でした。
- `/search` の `animation` は最初の 2 ページで各 8 件・総数 5796 で、ページ間の ID 重複なし。`date`・`views`・`likes` は該当項目の降順、`relevance` は日付順と異なります。総数は当時のスナップショットです。
- `tags=genshin_impact` の 8 件はすべてそのタグ付き。`tag` では 1 件のみ。`tags=genshin_impact,hatsune_miku` の 16 件はすべて両方を含みます。単数形の変換は廃止し、一括クエリの入口も修正しました。
- 存在しない `tags` の値は HTTP 500 になることがあり、サーバーエラーを「該当なし」と扱ってはいけません。
- ローカル辞書で「原神」は複数 ID に対応し、`ganshin` が先になって `hatsune_miku` との組み合わせは 0 件でした。`genshin_impact,hatsune_miku` と明示すれば結果が返ります。送信時に曖昧さを検査します。

### 画像キャッシュ
- カバーとアバターは必要なときに取得します。検索ページ：`data/img/search/`。購読のカバー：`data/img/sub/`（履歴に既にあるカバーを再利用し、ルールが生成したカバーは上書きしません。旧 `data/img/sub_video/` は該当項目の読み込み時に再利用）。アバター：`data/img/avatar/`（ファイル名は `username` から始まり、旧 `data/img/avatar_*` はコピー移行して購読行を更新）。
- 一覧のエンドポイントが ID しか返さない場合、カバー更新時に `GET /video/{id}` で `file.id`・`fileUrl`・`thumbnail` を補い、`https://{file-host}/image/original/{file-id}/thumbnail-{index:02d}.jpg` を取得します。解決した URL は購読データベースに書き戻します。
- ユーザーが今見ているカバーはバックグラウンドのカバーより先に取得され、失敗したカバーは次に要求されたときに再試行されます。
- ファイルは URL のフィンガープリントで命名し、一時ファイル経由でアトミックに置き換えます。ネットワーク失敗時はプレースホルダーのままです。

### 更新とキャッシュの並行設定
「設定 → 購読と更新 → 更新とカバーのパフォーマンス」で次の UI キーを制御します。
- `cover_download_workers_v1` — カバー取得の同時数、`1–16`、既定 `6`。
- `subscription_refresh_workers_v1` — 購読元更新の同時数、`1–8`、既定 `3`。各タスクは現在のトークンとプロキシを引き継ぐ独立した API セッションを使います。
- `subscription_incremental_refresh_v1` — アカウントフィードの増分更新、既定で有効。アカウントフィードとアカウントから取り込んだ作者に適用され、新しい順にページを読み、ローカルで既知の ID に当たった時点で止まります（既知の ID が無い初回は全件の索引を作ります）。プレイリストとローカルの作者は常に全件取得します。並行処理はネットワーク要求のみで、SQLite の書き込みは直列のままです。
- `home_cache_minutes_v1` — ホームの欄のキャッシュを新鮮と見なす時間（既定 15、0 は更新操作時のみ）。

### 結果表示
- グリッドはホーム・購読と共通の「カバーサイズ」スライダー（同じ部品・同じ保存サイズ）を使い、列数は利用可能な幅から決まります。
- リストは縮小画像を読み込まず、Iwara ID・タイトル・作者・再生数・いいね・コメント・公開日・タグ・リンクを表で表示します。「列設定」で変更でき、購読ページの列保存の仕組みを再利用します。

### Oreno3D オンライン検索
- 現在のページだけを取得し、画像キャッシュ以外はローカル DB に複製しません。
- 現在のページでは、必要に応じて Oreno3D の詳細ページから各カードの Iwara ID を解決し、`GET /video/{iwara_id}` でタイトル・作者・再生数・いいね・コメント・タグ・サムネイルを補います。最終リンクは `https://www.iwara.tv/video/{iwara_id}` で、Iwara の Web ページは解析せず、Oreno3D の詳細ページを最終ページにもしません。
- ブリッジは項目ごとに非同期で結果を返し、ID が分かった時点で開く・キューに入れるができます。設定で「バックグラウンドで事前解決」と「クリック／保存時に解決」を切り替え、同時数（1–8）も設定できます。
- Oreno3D では元サイトのサムネイルだけをカバーとして使います（Iwara のサムネイルは記録のみで再取得しません）。
- 種類はソースに従います。Oreno3D は動画とタグ、作者とプレイリストは Iwara ライブ API が担当します。
- Oreno3D は 1 ページ約 36 件で、ページ境界を保ち、結果ツールバーの左右ボタンで移動します。
- Oreno3D の検索は `GET /search` で `keyword` を送り、`sort`（`hot`・`favorites`・`latest`・`popularity`）と `page`（1 始まり）を追加します。`keyword` はタイトル・作者・タグ表示文への自由文検索で、複数語は空白かカンマで指定でき、`#タグ` や `|` は通常の文字です。タグ一覧は `/tags`、`/tag-groups/{id}`、`/tags/{id}?sort=latest&page=1` で、`/search` のパラメーターではありません。

### Oreno3D の動画と作者のフォールバック（2026-10-03 検証）
- 古い詳細ページには `h1.video-h1` が空のものがあっても、元動画と作者のリンクは残っています。パーサーはタイトルの代わりにソース ID で処理を続け、詳細ページの目印が無い応答は引き続きエラーとします。
- 元動画は `.video-figure a` と `a.video-watch-btn2` から取り、Iwara ドメインと `/video/{id}` のパスを検証します。コメントやサイドバーのリンクは走査しません。
- Oreno3D の作者 ID・名前・URL はリンク解決の結果と一緒に UI へ渡り、Iwara メタデータ取得の成否に依存しません。ID のみの応答や失敗応答が既存の `_iwara_metadata_loaded` を消すことはありません。
- 元動画から使える Iwara 作者が得られない場合、Oreno3D の作者ページ（最大 8 作品）を読み、アクセスできる動画の `user` からアカウントを割り出します。同一バッチの同じ作者は 1 回の探索を共有し、手動の作者操作でも使えます。
- 1・1000・9500 ページ目の 14 件はすべて動画と作者のリンクを取得でき、9600 ページ目で見つかった空タイトルの記録（[117917](https://oreno3d.com/movies/117917)、[120989](https://oreno3d.com/movies/120989)、[8350](https://oreno3d.com/movies/8350)）も修正後に解決しました。117917 の元動画は JSON `403 / errors.privateVideo` で、「削除済み」とは言えません。
- 候補がすべて使えない場合は Oreno3D の作者ページとエラーを残し、表示名からアカウントを推測しません。

### 多言語タグ候補
- タグ入力欄は英語・簡体字中国語・日本語の候補を提示し、クリック後も入力フォーカスと区切りを保ちます。結果は引き続き現在のソースに従います。
- 辞書は `data/iwara_tags.json` をオフラインで使い、「タグを更新」で LoveIwara の MIT 辞書を `data/tag_translations/` にキャッシュします。起動時にネットワークは不要です。
- 第三者のソースとライセンス：[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)。

## 7. NFO 生成とメディアセンター互換

ダウンロードルールで `NFO` を有効にすると、動画の隣に同名の `.nfo` を書き出します。Emby・Jellyfin・Kodi 向けの UTF-8 `<movie>` XML です。

- タイトル：`title`、`originaltitle`、`sorttitle`
- Iwara の識別子：`video_id`、`id`、`uniqueid type="iwara"`、`iwaraid`
- 取得元：`source`、`source_url`、`slug`、`quality`
- 作者：`author`（`director` と `studio` にも書き込み）
- 日付：`premiered`、`releasedate`、`year`、`published_at`
- 長さ：`runtime`（分）、`duration`（秒）、`fileinfo/streamdetails/video/durationinseconds`
- 評価と統計：`rating`、`mpaa`、`likes`、`views`、`comments`、および別名 `iwara_likes`、`iwara_views`、`iwara_comments`
- 説明とタグ：`plot`、`outline`、繰り返しの `genre`／`tag`、任意の `tags_json`
- カバー：ローカルのカバーが既にあるときだけ `thumb`（動画と同じフォルダーのファイル名）

標準フィールドと Iwara 固有フィールドを併記するので、メディアセンターが認識でき、Iwara の元情報も失われません。既存の NFO は起動時に自動では書き換えません。追加された標準フィールドが必要なら、再ダウンロード・メタデータの再生成・外部ツールを使ってください。

## 8. フィルター

グローバルフィルターは解決ステージで適用されます。

- 最小いいね、最小再生数、公開日レンジ、含めるタグ、除外タグ。
- タグ一致は大文字小文字を区別せず、区切りはカンマ・全角カンマ・空白・`;`・`|`。正規化したうえで API のタグ項目 `id`、`type`、`slug`、`name`、`title` と比較します。

## 9. UI が受け付ける URL 種別

- 単体動画：`https://www.iwara.tv/video/{id}`
- ユーザー：`https://www.iwara.tv/profile/{name}` または `/user/{name}`
- プレイリスト：`https://www.iwara.tv/playlist/{id}`
- 検索 URL：`https://api.iwara.tv/videos?...` または `https://www.iwara.tv/videos?...`

## 10. タグ収集スクリプトとローカル翻訳キャッシュ

- スクリプト：`app/core/crawl_iwara_tags.py`。`https://apiq.iwara.tv/tags?filter={A-Z0-9}&page={n}` を巡回し、三言語拡張可能なデータを `data/iwara_tags.json` と `docs/iwara_tags.md` に出力します。
- 各タグに `name_en`、`name_zh`、`name_ja` を生成します（既定は元のテキスト。`--translation-map path/to/map.json` で上書き可能）。
- 例：`pixi run python app/core/crawl_iwara_tags.py`

ローカル翻訳キャッシュ：
- プロジェクト生成のオフライン索引：`data/iwara_tags.json`。LoveIwara MIT キャッシュ：`data/tag_translations/loveiwara_iwara_tags_localized.json`。
- 検索ページは LoveIwara のキャッシュを優先し、無ければ `data/iwara_tags.json` にフォールバックします。「タグを更新」で必要なときだけ取得し、起動時にネットワークを必須としません。
- 同梱ソースは `app/data/tag_translations/loveiwara_iwara_tags_localized.json` と Oreno3D/Iwara タグ対応表 `app/data/oreno3d_iwara_map.json` で、Nuitka と GitHub Actions のビルドに明示的に含めます。
- 初回起動時、実行ファイル隣の `data/tag_translations/` にキャッシュが無い場合だけ内蔵辞書を展開します。既存のユーザーキャッシュは置き換えません。
