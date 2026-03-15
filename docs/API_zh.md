# IwaraTool API 说明（简体中文）

[English](./API.md) | [日本語](./API.ja.md)

本文档说明当前项目中已实现的 API 能力。

## 1. 认证

### 登录
- 接口：`POST https://api.iwara.tv/user/login`
- 请求体：
  - `email`：用户名或邮箱
  - `password`：账号密码
- 成功后：
  - 返回 `token`（Bearer Token）

### Token 用法
- 鉴权请求头：
  - `Authorization: Bearer <token>`
- Token 本地缓存到 `data/config.ini`：
  - `auth_token`
  - `auth_token_saved_at`
- 启动策略：
  - 优先使用本地 token（快速路径）
  - 无 token 时再回退账号密码登录

## 2. 视频信息与下载直链解析

### 获取视频元信息
- 接口：`GET https://api.iwara.tv/video/{video_id}`

### 解析可下载源
- 输入：视频元信息中的 `fileUrl`
- 追加 `X-Version` 请求头，计算基于：
  - `{filename}_{expires}_{salt}`
  - SHA1
- 内置画质回退：
  - `Source -> 540 -> 360`

## 3. 批量来源

### 按用户
- 先解析用户 id：
  - `GET https://api.iwara.tv/profile/{username}`
- 再拉取视频列表：
  - `GET https://api.iwara.tv/videos?user={user_id}&sort=date&page={n}`

### 按播放列表
- `GET https://api.iwara.tv/playlist/{playlist_id}?page={n}`

## 4. 搜索下载（新增）

程序已支持在下载输入框直接粘贴搜索 URL。

### 支持格式
- `https://api.iwara.tv/videos?...`
- `https://www.iwara.tv/videos?...`
- 示例：
  - `https://api.iwara.tv/videos?tags=2d&sort=date`
  - `https://www.iwara.tv/videos?tags=2d&sort=date`

### 行为
- 解析 URL 查询参数
- 分页请求 `GET /videos`
- 自动将结果中的视频 ID 入队

### 搜索专属数量上限
- 设置项：`Search Download Limit`
- 配置键：
  - `search_limit_enabled`（bool）
  - `search_limit_count`（int）
- 启用后只会入队前 `N` 条搜索结果
- 若 URL 自带 `limit`，最终上限为 `min(url limit, setting limit)`

## 5. 筛选

全局筛选开关在解析阶段生效。

### 支持筛选项
- 最小点赞
- 最小播放
- 发布日期区间
- 包含标签（正筛）
- 排除标签（反筛）

### 标签匹配规则
- 大小写不敏感
- 支持分隔符：
  - 逗号、中文逗号、空格、`;`、`|`
- 归一化对比字段：
  - `id`、`type`、`slug`、`name`、`title`

## 6. UI 支持的输入 URL 类型

- 单视频：
  - `https://www.iwara.tv/video/{id}`
- 用户主页：
  - `https://www.iwara.tv/profile/{name}` 或 `/user/{name}`
- 播放列表：
  - `https://www.iwara.tv/playlist/{id}`
- 搜索 URL：
  - `https://api.iwara.tv/videos?...`
  - `https://www.iwara.tv/videos?...`

## 7. 标签抓取脚本（为后续一键标签筛选做准备）

- 脚本路径：
  - `app/core/crawl_iwara_tags.py`
- 作用：
  - 直接调用 `/tags` 接口，按 `A-Z` 和 `0-9` 抓取标签
  - 导出三语可扩展的数据到 JSON + Markdown
- 接口模式：
  - `https://apiq.iwara.tv/tags?filter={A-Z0-9}&page={n}`
- 默认输出：
  - `data/iwara_tags.json`
  - `docs/iwara_tags.md`
- 翻译字段：
  - 每个 tag 都生成 `name_en`、`name_zh`、`name_ja`
  - 默认回退为原始 tag 文本
  - 可通过 `--translation-map path/to/map.json` 覆盖
- 示例：
  - `pixi run python app/core/crawl_iwara_tags.py`
