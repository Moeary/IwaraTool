# IwaraTool

[English Version](#english)

一个基于 Python 和 pysid6+widget-fluent 开发的 Iwara 视频下载器。非常适合批量下载单作者的所有视频!

![](https://raw.githubusercontent.com/Moeary/pic_bed/main/img/202603051905789.png)

## 核心功能
- X-Version 签名计算，确保下载请求正常通行
- 画质自动回退机制（Source -> 540 -> 360）
- 防 URL 过期处理，多阶段状态机控制并发下载
- 本地 SQLite 历史记录，自动跳过已经下过的视频防止重复下载
- 支持输入用户主页或播放列表链接，自动分页解析所有视频并排队下载

## 怎么用
去release里面下载你操作系统的文件,然后保存到本地运行即可

**重点：先登录！** 不登录下个锤子。
请先在设置页面里把账号登录好(网页，确认登录成功了，再去粘贴你想下载的视频、用户或者播放列表链接。

## 运行与构建
项目依赖使用 pixi 管理。
如果你是开发者想直接运行源代码：
```shell
pixi run start //运行程序
pixi run build //构建应用
```
## 参与贡献
非常欢迎大家提 PR！
目前程序的 i18n（多语言）还没做完，深色/浅色模式切换的时候也有点小 bug。这些虽然都不影响核心的下载功能，但如果你有时间并且愿意帮忙完善它的话，直接提 PR 就行，感谢！

## License
MIT License

```
下载本程序即视为同意遵守 MIT 许可证。详情请参阅 LICENSE 文件。
```

1. 禁止将本项目用于任何违法、违规或违背公共秩序与善良风俗的用途；如因违规使用导致损失，责任由用户自行承担。
2. 项目提供的打包版本及脚本仅供个人学习与研究使用，未经许可不得用于商业再发行或转售。
3. 项目维护者保留依据法律法规或社区反馈随时更新、暂停或终止服务与支持的权利。

## 特别感谢

感谢[hare1039](https://github.com/hare1039)的[iwara-dl](https://github.com/hare1039/iwara-dl/tree/master)项目提供的宝贵参考和启发，尤其是在 X-Version 签名计算和下载链接解析方面的实现细节，对本项目的开发起到了重要的推动作用。

---

<a id="english"></a>
# IwaraTool (English)

A Python and Pyside6 based video downloader for Iwara. It is especially good at batch downloading all videos from a single creator!

![](https://raw.githubusercontent.com/Moeary/pic_bed/main/img/202603051905789.png)


## Features
- Valid X-Version signature calculation for API requests
- Automatic video quality fallback (Source -> 540 -> 360)
- Anti-expiration link handling with a multi-stage state machine
- Duplicate prevention using a local SQLite history database
- Resumable downloads
- Batch downloading for user profiles and playlists with automatic pagination

## How to Use
**Important: Log in first!** You won't be able to download anything without an account.
Please log in using the built-in browser/login page first. Once logged in, you can paste video, user, or playlist URLs to start downloading.

## Run and Build
This project uses pixi for package management.
- Run locally: pixi run start
- Build app: pixi run build

## Contributing
Pull Requests are highly welcome! 
Currently, the i18n (internationalization) implementation is incomplete, and there is a minor visual bug when toggling between dark and light modes. These issues do not affect the core downloading functionality, but if you'd like to help fix them, your contributions are greatly appreciated!
