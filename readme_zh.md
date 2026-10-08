# IwaraTool

![logo](./docs/iwaratool_logo.png)

[English](./readme.md) | [日本語](./readme_ja.md)

告别繁琐的命令行！拥有现代化 Fluent 风格界面的 Iwara 批量下载器，小白也能一键下载作者全视频。

![demo](./docs/iwaratool_demo_v0.6.gif)

文档：[Wiki](https://github.com/Moeary/IwaraTool/wiki) · [API 文档](./docs/API_zh.md) · [标签索引](./docs/iwara_tags.md)

## 核心功能
- 批量下载：支持作者页、播放列表和搜索链接批量入队；任务队列持久化，安全退出后自动恢复。
- 下载调度：支持 `X-Version` 签名计算，画质自动回退（`Source -> 540 -> 360`），状态机调度减少 URL 过期问题。
- 本地去重与 SQLite 历史记录中心，支持搜索、筛选、排序与清理。
- 搜索：Iwara 实时视频/作者/播放列表搜索，Oreno3D 在线视频搜索，中日英标签候选；结果显示本地下载状态。
- 订阅管理：关注账户、作者与播放列表的订阅，支持刷新追踪、新增统计和列表/封面视图。
- 下载规则：统一管理点赞、播放、日期、标签、标题关键词、命名模板和下载行为，可生成 NFO。
- 修复中心：批量重命名已有视频，或按规则整理到输出目录，并检查目标冲突。
- 界面：中/英/日语言与深浅色主题可运行时切换；表格列可配置并持久化。
- 可选 aria2 RPC、封面下载和 `.nfo` 生成。
- 稳健性：下载完成度校验与断点续传校验，API 请求自动退避重试并限速，SQLite WAL，滚动日志（`data/logs/`）。
- 维护：系统托盘与开机启动、不含凭据的数据备份/恢复、SHA-256 校验的一键更新（Windows 打包版）。

## 快速开始
1. 从 [Releases](https://github.com/Moeary/IwaraTool/releases) 下载最新版本。
   - Linux 预编译包基于 GitHub 的 `ubuntu-latest` 构建，不支持 glibc 较老的系统；老版本发行版建议下载源码后在本机编译。
2. 打开程序；下载私有视频或导入关注作者时需要登录。
3. 在 `下载工作台` 粘贴链接，按需选择下载规则后提交。
4. 在 `订阅页` 跟踪账号流、作者或播放列表，并批量下载新增内容。

更多细节（修复中心、搜索下载标记、Oreno3D 作者回退、关键词/标签搜索、订阅封面缓存）见 [使用指南](./docs/guide/usage.md)；本地数据与缓存见 [本地数据与缓存](./docs/guide/data.md)。

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
`tags` 支持详见 [标签索引](./docs/iwara_tags.md)。

## 页面展示
1. 下载工作台
![下载工作台](./docs/panel_view/iwaratool_download_panel.jpg)
2. 搜索页
![搜索页](./docs/panel_view/iwaratool_search_panel.jpg)
3. 订阅页
![订阅页](./docs/panel_view/iwaratool_subscription_panel.jpg)
4. 历史记录页
![历史记录页](./docs/panel_view/iwaratool_history_panel.jpg)
5. 规则页
![规则页](./docs/panel_view/iwaratool_rule_panel.jpg)
6. 设置页
![设置页](./docs/panel_view/iwaratool_settings_panel.jpg)

## 运行与构建

项目依赖使用 [pixi](https://pixi.prefix.dev/latest/) 管理。

如果你是开发者想直接运行源代码/或者想构建应用：

```shell
pixi install
pixi run start  # 运行程序
pixi run build  # 构建应用
pixi run crawl  # 抓取标签数据（更新 docs/iwara_tags.md）
pixi run test   # 运行测试
```

## 参与贡献

非常欢迎大家提 PR！
请将改动提交到 `dev` 分支，保持英文、简体中文和日文界面文本同步，并在提交前运行完整测试。涉及界面变化时，建议附上截图或短视频。

## 许可证

MIT License。

**下载本程序即视为同意遵守 MIT 许可证。详情请参阅 LICENSE 文件。**

1. 禁止将本项目用于任何违法、违规或违背公共秩序与善良风俗的用途；如因违规使用导致损失，责任由用户自行承担。
2. 项目提供的打包版本及脚本仅供个人学习与研究使用，未经许可不得用于商业再发行或转售。
3. 项目维护者保留依据法律法规或社区反馈随时更新、暂停或终止服务与支持的权利。

## 特别感谢

感谢[hare1039](https://github.com/hare1039)的[iwara-dl](https://github.com/hare1039/iwara-dl/tree/master)项目提供的宝贵参考和启发，尤其是在 X-Version 签名计算和下载链接解析方面的实现细节，对本项目的开发起到了重要的推动作用。
