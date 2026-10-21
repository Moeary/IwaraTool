# 参考：平台说明、链接类型、内部机制与构建

[English](./reference_en.md) | [日本語](./reference_ja.md) | [返回 README](../../readme_zh.md)

这里是大多数用户用不到的细节；README 只保留上手所需的内容。

## 平台说明

- **Windows**：从 [Releases](https://github.com/Moeary/IwaraTool/releases) 下载 `.exe` 直接运行。登录自启动与一键更新（SHA-256 校验）仅支持 Windows。
- **Linux**：预编译包基于 GitHub 的 `ubuntu-latest` 构建，不支持 glibc 较老的系统；老版本发行版建议下载源码后在本机编译。
- **macOS**：提供 Apple Silicon（ARM64）和 Intel（AMD64）ZIP，解压后将 `IwaraTool.app` 拖入“应用程序”。CI 在 macOS 15 验证，更旧版本尚未验证。应用未使用 Developer ID 签名与公证；若被系统阻止，请按“隐私与安全性”中的提示允许打开。
  - 数据保存在 `~/Library/Application Support/IwaraTool/`，默认下载至 `~/Downloads/IwaraTool/`。
  - 更新请下载并替换 `.app`，数据会保留。

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

Windows 构建会写入版本信息；设置环境变量 `WINDOWS_SIGN_PFX`（.pfx 路径，或 CI 中用 `WINDOWS_SIGN_PFX_BASE64`）与 `WINDOWS_SIGN_PFX_PASSWORD` 后，`pixi run build` 会自动做 Authenticode 签名并加时间戳（有 Windows SDK 时用 signtool，否则用 PowerShell）。也可用 `python scripts/build_windows.py --sign-only <exe>` 只签名已有文件。
