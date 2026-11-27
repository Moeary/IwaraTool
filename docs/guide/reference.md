# 参考：平台说明、链接类型、内部机制与构建

[English](./reference_en.md) | [日本語](./reference_ja.md) | [返回 README](../readme/readme_zh.md)

这里是大多数用户用不到的细节；README 只保留上手所需的内容。

## 平台说明

- **Windows**：从 [Releases](https://github.com/Moeary/IwaraTool/releases) 下载 `IwaraTool-Windows-AMD64-<版本>.zip`，解压后运行 `IwaraTool.exe`；数据保存在程序旁的 `data/`，解压到旧版所在文件夹即可沿用旧数据。支持托盘图标与开机自启。
- **macOS**：提供 Apple Silicon（ARM64）的 ZIP，解压得到 `IwaraTool.app` 后拖入“应用程序”。CI 在 macOS 15 验证，更旧版本尚未验证。应用未使用 Developer ID 签名与公证；首次启动被拦截时的处理与代理配置见 [安装与上手](./install.md)。
  - 数据保存在 `~/Library/Application Support/IwaraTool/`，默认下载至 `~/Downloads/IwaraTool/`。
  - 更新请替换 `.app`，数据会保留。
- **Android**：`IwaraTool-Android-<版本>.apk`。所有版本使用同一把长期签名密钥，新版可直接覆盖安装升级，数据保留。
- **Linux / macOS Intel**：暂无预编译包，可从源码运行（见下文）。

## 支持的 URL 类型

```text
https://www.iwara.tv/profile/username
https://www.iwara.tv/profile/username/videos
https://www.iwara.tv/playlist/xxxxxxxx
https://www.iwara.tv/video/xxxxxxxx
https://www.iwara.tv/videos?sort=date
https://www.iwara.tv/videos?tags=2d&sort=likes
https://api.iwara.tv/videos?tags=2d&sort=date
```

`sort` 支持：`date`、`trending`、`popularity`、`views`、`likes`。

`tags` 支持详见 [标签索引](../iwara_tags.md)。

## 命名规则

命名规则决定每个视频保存在下载目录下的哪个位置。在规则编辑页可以直接选预设，也可以逐个选字段、再填写字段之间的文字（`_`、`-`、空格，或用 `/` 进入下一级文件夹）拼出来；完整规则仍可手动编辑，并带实时预览。

| 占位符 | 含义 |
| --- | --- |
| `{author}` | 作者账号 ID（例如 `user154126`） |
| `{username}` | 作者昵称 |
| `{title}` / `{id}` / `{slug}` | 视频标题 / ID / 链接别名 |
| `{YYYY-MM-DD}` `{date}` / `{YYYY}` / `{MM}` / `{DD}` | 发布日期及其年、月、日 |
| `{quality}` | 下载的画质 |
| `{views}` `{likes}` `{comments}` | 播放、点赞、评论数 |
| `{duration}` | 时长（秒） |
| `{rating}` | 内容分级 |

末尾会自动补上 `.mp4`；Windows 保留名、过长路径和非法字符会被自动处理。

## 内部机制

- **下载调度**：支持 `X-Version` 签名计算，画质自动回退（`Source -> 540 -> 360`），状态机调度减少 URL 过期问题；可选 aria2 RPC。
- **稳健性**：下载完成度校验与断点续传校验，API 请求自动退避重试并限速，SQLite WAL，滚动日志（`data/logs/`）。
- **持久化**：任务队列在安全退出后自动恢复；历史与订阅保存在 SQLite。详见 [本地数据与缓存](./data.md)。
- **搜索**：Iwara 实时 API 加 Oreno3D 桥接；标签候选来自内置的中日英词典。
- **API 说明**：[API_zh.md](../API_zh.md)。

## 从源码运行 / 构建

依赖使用 [pixi](https://pixi.prefix.dev/latest/) 管理。

```shell
pixi install
pixi run start  # 运行程序
pixi run build  # 构建应用
pixi run crawl  # 抓取标签数据（更新 docs/iwara_tags.md）
pixi run test   # 运行测试
```

`pixi run build` 用 `flet build` 打包当前系统的桌面版（`--platform apk` 打包 Android，需要 Android SDK，平时由 CI 构建）。Android 发布版用同一把长期密钥签名：设置 `FLET_ANDROID_SIGNING_KEY_STORE`（.jks 路径）、`FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD` 与 `FLET_ANDROID_SIGNING_KEY_ALIAS` 后构建，脚本会核对 APK 证书与 `scripts/tools/build.py` 的 `RELEASE_CERT_SHA256` 一致；CI 从仓库 secrets `ANDROID_KEYSTORE_BASE64`、`ANDROID_KEYSTORE_PASSWORD`、`ANDROID_KEY_ALIAS` 读取，打标签发版时缺少密钥会直接失败。