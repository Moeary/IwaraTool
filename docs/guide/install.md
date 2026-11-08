# 安装与上手

[English](./install_en.md) | [日本語](./install_ja.md) | [返回 README](../../readme_zh.md)

本文说明如何选择安装包、在各平台安装、处理 macOS 首次启动的安全提示、配置代理，以及最基本的使用流程。

## 选择安装包

在 [Releases](https://github.com/Moeary/IwaraTool/releases) 中按系统下载：

| 系统 | 文件 | 说明 |
| --- | --- | --- |
| Windows 10 / 11（64 位） | `IwaraTool-Windows-AMD64-<版本>.exe` | 单文件，双击运行 |
| Linux（x86_64） | `IwaraTool-Linux-AMD64-<版本>` | 基于 `ubuntu-latest` 构建，glibc 较老的发行版请从源码运行 |
| macOS · Apple Silicon（M 系列芯片） | `IwaraTool-macOS-AppleSilicon-ARM64-v<版本>.dmg` | 推荐 |
| macOS · Intel 处理器 | `IwaraTool-macOS-Intel-x86_64-v<版本>.dmg` | 推荐 |

每个 macOS 版本另附同名 `.zip`，内容与 DMG 相同，供习惯解压安装的用户使用。所有文件都附带 `.sha256` 校验值。

**如何确认 Mac 的芯片**：点击屏幕左上角  →“关于本机”。“芯片”一栏写着 **Apple M1 / M2 / M3 / M4…** 时选 AppleSilicon-ARM64；写着“处理器 … Intel Core …”时选 Intel-x86_64。Apple Silicon 的 Mac 也能借助 Rosetta 运行 Intel 版，但速度较慢；Intel Mac 无法运行 ARM64 版。CI 在 macOS 15 上验证，更旧的系统版本尚未验证。

## macOS：用 DMG 安装

1. 双击下载的 `.dmg`，打开安装窗口。
2. 按窗口中的箭头，把 **IwaraTool** 图标拖到右侧的 **Applications** 文件夹。
3. 拖动完成后，在访达侧栏点击推出按钮卸下“IwaraTool”磁盘，之后可以删除 `.dmg`。
4. 从“应用程序”文件夹或启动台打开 IwaraTool。不要直接在 DMG 窗口里运行——DMG 是只读的，更新时也无法替换。

更新时重复以上步骤，选择“替换”即可；设置、历史记录和订阅都保存在 `~/Library/Application Support/IwaraTool/`，不会因替换应用而丢失。

## macOS：首次启动的安全提示

IwaraTool 没有付费的 Apple Developer ID 签名，也未经 Apple 公证，所以第一次打开时系统会拦截。这是 macOS 对所有未公证应用的统一处理，按下面的方法允许一次即可，之后正常打开。

**macOS 15（Sequoia）及更新版本**

1. 先双击打开一次 IwaraTool，在提示“Apple 无法验证……”的对话框中点“完成”。
2. 打开“系统设置”→“隐私与安全性”，滚动到最下方的“安全性”区域。
3. 在“已阻止使用 IwaraTool，因为来自身份不明的开发者”旁点击 **“仍要打开”**，输入登录密码。
4. 在随后的对话框中再点一次“仍要打开”。

**macOS 14（Sonoma）及更早版本**：在“应用程序”文件夹中按住 Control 键点按 IwaraTool（或右键）→“打开”，再在对话框中点“打开”。

**提示“已损坏，无法打开，你应该将它移到废纸篓”**：通常是浏览器给文件加上的隔离属性所致，并非文件真的损坏。确认文件来自本项目的 Releases 页面后，在“终端”中执行：

```bash
xattr -dr com.apple.quarantine /Applications/IwaraTool.app
```

然后重新打开即可。

## 代理配置

访问 Iwara 通常需要代理。IwaraTool 的“登录/解析代理”和“视频下载代理”**默认都处于关闭状态**，此时请求跟随系统代理或 TUN：

- 代理软件已开启 **系统代理** 或 **TUN 模式**：无需任何设置。
- 两者都没开：打开“应用设置 → 代理设置”，启用“登录/解析代理”，填写代理软件的本地 HTTP / 混合端口，然后点“应用”。

| 代理软件 | 常见默认地址 |
| --- | --- |
| Clash Verge Rev（预填值） | `http://127.0.0.1:7897` |
| 旧版 Clash for Windows / ClashX 等 | `http://127.0.0.1:7890` |
| 其他软件 | 以软件设置中显示的端口为准，可填写任意端口 |

“视频下载代理”只影响视频文件本身（以及 aria2 的 `all-proxy`），封面、头像和元数据始终跟随“登录/解析代理”。从旧版本升级的用户会保留原来的代理设置，不受新默认值影响。Linux 不会读取桌面环境的系统代理，请在程序内启用代理，或设置 `HTTPS_PROXY` 等环境变量。

## 基本使用流程

1. **登录（可选）**：在“应用设置 → 账号登录”中登录。下载私有视频、查看“我的订阅”或导入关注的作者时需要登录。
2. **下载**：在“下载工作台”粘贴作者、播放列表、搜索页或单个视频的链接，选择下载规则后提交。未完成的任务下次打开时会继续。
3. **搜索**：在“搜索”页选择数据源与类型并输入关键词。Iwara 的搜索接口无法预先限定点赞、时长等条件，可在结果出来后点“筛选”按钮，在弹窗中设置条件并重新排序（详见 [使用指南](./usage.md#搜索后筛选)）。勾选结果后点“加入选中项”即可下载。
4. **订阅**：在搜索结果或作者页订阅作者 / 播放列表，之后在“订阅页”查看新作品并只下载新增部分。

更多细节见 [使用指南](./usage.md) 与 [参考文档](./reference.md)。

## 常见问题

**首页或搜索一直加载失败**：多半是网络无法直连 Iwara。确认代理软件正在运行，并按上文开启系统代理 / TUN，或在程序内填写正确端口。Clash Verge Rev 默认是 `7897`，不是旧教程中常见的 `7890`。

**macOS 提示“无法打开”或“已损坏”**：见上文“首次启动的安全提示”。若在 Intel Mac 上打开 ARM64 版，系统会提示不支持此类 Mac，请改下 Intel-x86_64 版。

**数据保存在哪里、如何卸载**：macOS 的设置与缓存位于 `~/Library/Application Support/IwaraTool/`，视频默认下载到 `~/Downloads/IwaraTool/`。卸载时把“应用程序”中的 IwaraTool 移到废纸篓；若不再需要数据，再删除上述数据目录。Windows / Linux 版的数据位于程序旁的 `data/` 文件夹。详见 [本地数据与缓存](./data.md)。

**如何更新**：Windows 版可在“应用设置 → GitHub Release 更新”中一键更新；macOS 和 Linux 请下载新版本替换旧文件，数据会保留。
