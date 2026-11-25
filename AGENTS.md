# AGENTS.md（临时：Flet 前端移植期间）

本文件只在 `feat/flet-ui` 分支的移植期间有效，Flet 版替换旧界面或放弃移植时删除。仓库 `.gitignore` 忽略了 `agents.md`，本文件用 `git add -f` 跟踪。

## 目标与依据

- 为 IwaraTool 新增 Flet 前端（v1.3.0），**优先 Windows 与 macOS 桌面**；Android 等移动端放到桌面版可用之后。
- 执行依据：[`docs/flet_ui_plan.md`](docs/flet_ui_plan.md)（工程、核心整理、设计语言、响应式、逐页方案、阶段计划）。可行性背景：[`docs/flet_migration.md`](docs/flet_migration.md)。
- **设计不必照搬旧界面**：方案里的页面设计是起点，有更好的交互或布局就采用，并把取舍补进方案第 10 节的“功能取舍”表。功能不能悄悄丢失。
- 旧界面截图基线（离线虚构数据，中文，浅色/深色）在 `tmp/pyside6_baseline/`（未纳入版本库），用 `pixi run python scripts/tools/record_demo.py --no-gif --lang zh --theme <light|dark> --out tmp/pyside6_baseline/<主题>` 重新生成。

## 硬性约束（摘自方案第 0 节）

1. 旧界面 `app/ui/` 与 `main.py` 在替换前保持可运行、可测试、可发版，不为新界面改变其行为。
2. 新界面只放在 `app/ui_flet/`，入口 `main_flet.py`。`app/ui_flet` 禁止导入 `app.ui`、`PySide6`、`shiboken6`、`qfluentwidgets`、`qframelesswindow`（`scripts/tests/flet_ui/test_flet_theme.py` 强制检查）。
3. 业务逻辑只在 `app/core/`；界面无关的展示逻辑放 `app/presentation/`（不导入 Qt 或 Flet）。旧界面里需要复用的逻辑先迁出再两边调用，**不得复制**到 `app/ui_flet`。
4. Flet 事件处理函数不得在事件循环上做阻塞调用（HTTP、SQLite 批量、文件扫描、FFmpeg），用 `asyncio.to_thread` 或 `page.run_thread`。
5. 用户可见文字一律 `app.i18n.tr(en, zh, ja)`。

## 主题与图标（全局统一）

- 颜色、间距、圆角、尺寸、字号、动效只从 `app/ui_flet/tokens.py` 取；页面里不写十六进制颜色（测试检查 `app/ui_flet/**` 中除 `tokens.py` 外不得出现颜色字面量）。
- `app/ui_flet/theme.py` 把令牌转成 Flet `Theme`：`apply_theme(page, theme_mode=..., language=...)`。Material 没有对应角色的颜色（状态色、封面遮罩）用 `palette_for(page)`；文字样式用 `text_style(TYPE.xxx)`。
- 图标只从 `app/ui_flet/icons.py` 取（`NavIcon` / `StateIcon` / `AppIcon`），不得直接写 `ft.Icons.X`（测试检查）。规则：静止为线框 `_OUTLINED`，导航选中与开关激活为实心 `_ROUNDED`。新图标先加进注册表。
- 状态徽章统一用 `components/status_badge.py`。
- 改主题后用样张检查两种模式：`pixi run -e flet theme-gallery [--mode dark] [--lang ja]`，截图 `--shot tmp/flet_theme/x.png`。
- 新增颜色须通过 `flet_ui/test_flet_theme.py` 的 WCAG 对比度检查（正文 4.5:1，图标 3:1）。

## 环境与命令

| 用途 | 命令 |
| --- | --- |
| 旧界面运行 / 测试 | `pixi run start` / `pixi run test` |
| Flet 运行（独立数据目录 `data-flet/`） | `pixi run -e flet start-flet` |
| Flet 测试（core + flet_ui） | `pixi run -e flet test-flet` |
| 主题样张 | `pixi run -e flet theme-gallery` |
| Flet 打包（本机桌面平台，含审计与冒烟） | `pixi run -e flet build-flet` |
| 尺寸验收截图（9.2 清单，临时数据目录） | `pixi run -e flet python scripts/tools/flet_viewports.py --out tmp/flet_viewports/x [--route /search] [--theme dark] [--lang en] [--account] [--only 360x800]` |
| 单次指定尺寸启动 / 截图 | `pixi run -e flet start-flet --viewport 360x800 [--route ...] [--shot 绝对路径.png]` |

- pixi 分 `qt`（default 环境，Python 3.11，PySide6 + Nuitka）与 `flet`（Python 3.12，Flet 1.1.3，不含 PySide6）两个 feature。旧界面暂不能升 3.12（Nuitka 包导入 shiboken6 6.6.2 段错误，见方案 2.3）。
- 测试目录：`scripts/tests/core`（不依赖 Qt，两个环境都跑）、`qt`（需要 PySide6 或 `app/ui`）、`flet_ui`（Flet 界面；依赖 Flet 的用例 `skipUnless(HAS_FLET)`）。新测试放进对应目录；不能用 `flet` 作目录名。
- 源码运行的数据目录是 `data-flet/`，打包后是 Flet 提供的 `FLET_APP_STORAGE_DATA`，都不与旧界面共用。入口先调用 `runner.prepare_environment()` 再导入 `app.config`。
- Flet 入口一律用 `app/ui_flet/runner.py` 的 `run(main)` 启动，不直接 `ft.run`：`flet_desktop` 会优先启动 `<当前目录>/build/macos/*.app`、`build/windows/*.exe`，而这里放的是 Nuitka 构建的旧版应用；`run` 会切到中立工作目录（相对的 `IWARATOOL_DATA_DIR` 等先转为绝对路径）。
- Flet 首次启动会从 GitHub 下载桌面客户端（`~/.flet/client`）。本机 Python 内下载可能卡住时，先用 curl 下载 `https://github.com/flet-dev/flet/releases/download/v1.1.3/flet-macos.tar.gz`（Windows 为 `flet-windows.zip`），再设 `FLET_CLIENT_URL=file:///<路径>` 运行一次。

## 分层落点（P1 之后）

- 阻塞的业务流程在 `app/core`（如 `search/service.py`、`home/loading.py`、`search/resolution.py`、`subscription/batch.py`），接收 `manager` / `client` 参数与 `should_stop`，默认跟随当前 `CancelToken`（`core/cancellation.py`）。
- 展示用的文字、派生数据与规则在 `app/presentation`（导航返回栈、快捷键目录、点击动作、入队与暂存提示等）；颜色只给语义角色名，由各界面映射。
- 启动与关闭只走 `app/bootstrap.py`；Flet 侧用 `app/ui_flet/lifecycle.py`（关窗先 `stop` 再销毁窗口）和 `app/ui_flet/services/events.py` 的 `EventBridge`（页面用 `SubscriptionGroup`，卸载时 `close()`）。
- 本机直接用 `pixi run python -m unittest ...` 跑 `qt` 用例时要设 `QT_QPA_PLATFORM=cocoa`（`pixi run test` 已设置），否则创建主窗口时 qframelesswindow 段错误。依赖窗口激活的焦点类用例在桌面被其他应用占用焦点时会超时失败，与代码无关，以 CI 结果为准。

## 外壳与声明式写法（P2 之后）

- 根组件 `app/ui_flet/app.py` 的 `mount(page)`；页面按目的地在 `app/ui_flet/pages/` 用 `register(destination, Component)` 注册，组件签名 `(destination, path)`，外框用 `components/page_frame.py` 的 `PageFrame`。
- 组件通过 `stores/app_state.py` 的 `use_shell()` 取 `Shell`（状态 + 动作）。`ft.use_context` 只对 `Observable` 值自动订阅，`Shell` 不是，所以 `use_shell()` 会额外 `use_state(shell.state)` 订阅；新组件不要直接 `use_context(ShellContext)`。
- 页面需要随返回恢复的状态，给 `shell.navigator.destinations[name]` 设 `snapshot_state` / `restore_state`。
- `run()` 会切换工作目录：脚本里的输出路径一律先 `.resolve()` 成绝对路径，否则写文件失败后窗口不关、进程挂住。

## 提交与 CI

- 在 `feat/flet-ui` 上工作，不直接提交到 `dev` / `main`。
- 每完成一个合适的小单元（能运行、测试通过）就本地提交，**不推送**。
- 关键节点（阶段完成、环境/CI/打包改动、跨平台风险较大的改动）提交并推送，然后观察 Tests 工作流各平台结果（`gh run list --branch feat/flet-ui`、`gh run watch`），失败就修。
- 提交信息沿用仓库风格（英文祈使句标题），不加 AI 署名或 Co-Authored-By。
- 完成后同步更新 `docs/flet_ui_plan.md` 中对应的【需核实】结论与阶段进度。
