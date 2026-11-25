# AGENTS.md（临时：Flet 前端移植期间）

本文件只在 `feat/flet-ui` 分支的移植期间有效，Flet 版替换旧界面或放弃移植时删除。仓库 `.gitignore` 忽略了 `agents.md`，本文件用 `git add -f` 跟踪。

## 目标与依据

- 为 IwaraTool 新增 Flet 前端（v1.3.0），**优先 Windows 与 macOS 桌面**；Android 等移动端放到桌面版可用之后。
- 执行依据：[`docs/flet_ui_plan.md`](docs/flet_ui_plan.md)（工程、核心整理、设计语言、响应式、逐页方案、阶段计划）。可行性背景：[`docs/flet_migration.md`](docs/flet_migration.md)。
- **设计不必照搬旧界面**：方案里的页面设计是起点，有更好的交互或布局就采用，并把取舍补进方案第 10 节的“功能取舍”表。功能不能悄悄丢失。
- 旧界面截图基线（离线虚构数据，中文，浅色/深色）在 `tmp/pyside6_baseline/`（未纳入版本库），用 `pixi run python scripts/tools/record_demo.py --no-gif --lang zh --theme <light|dark> --out tmp/pyside6_baseline/<主题>` 重新生成。

## 硬性约束（摘自方案第 0 节）

1. 旧界面 `app/ui/` 与 `main.py` 在替换前保持可运行、可测试、可发版，不为新界面改变其行为。
2. 新界面只放在 `app/ui_flet/`，入口 `main_flet.py`。`app/ui_flet` 禁止导入 `app.ui`、`PySide6`、`shiboken6`、`qfluentwidgets`、`qframelesswindow`（`scripts/tests/test_flet_theme.py` 强制检查）。
3. 业务逻辑只在 `app/core/`；界面无关的展示逻辑放 `app/presentation/`（不导入 Qt 或 Flet）。旧界面里需要复用的逻辑先迁出再两边调用，**不得复制**到 `app/ui_flet`。
4. Flet 事件处理函数不得在事件循环上做阻塞调用（HTTP、SQLite 批量、文件扫描、FFmpeg），用 `asyncio.to_thread` 或 `page.run_thread`。
5. 用户可见文字一律 `app.i18n.tr(en, zh, ja)`。

## 主题与图标（全局统一）

- 颜色、间距、圆角、尺寸、字号、动效只从 `app/ui_flet/tokens.py` 取；页面里不写十六进制颜色（测试检查 `app/ui_flet/**` 中除 `tokens.py` 外不得出现颜色字面量）。
- `app/ui_flet/theme.py` 把令牌转成 Flet `Theme`：`apply_theme(page, theme_mode=..., language=...)`。Material 没有对应角色的颜色（状态色、封面遮罩）用 `palette_for(page)`；文字样式用 `text_style(TYPE.xxx)`。
- 图标只从 `app/ui_flet/icons.py` 取（`NavIcon` / `StateIcon` / `AppIcon`），不得直接写 `ft.Icons.X`（测试检查）。规则：静止为线框 `_OUTLINED`，导航选中与开关激活为实心 `_ROUNDED`。新图标先加进注册表。
- 状态徽章统一用 `components/status_badge.py`。
- 改主题后用样张检查两种模式：`pixi run -e flet theme-gallery [--mode dark] [--lang ja]`，截图 `--shot tmp/flet_theme/x.png`。
- 新增颜色须通过 `test_flet_theme.py` 的 WCAG 对比度检查（正文 4.5:1，图标 3:1）。

## 环境与命令

| 用途 | 命令 |
| --- | --- |
| 旧界面运行 / 测试 | `pixi run start` / `pixi run test` |
| Flet 运行（独立数据目录 `data-flet/`） | `pixi run -e flet start-flet` |
| Flet 测试（`test_flet_*.py`） | `pixi run -e flet test-flet` |
| 主题样张 | `pixi run -e flet theme-gallery` |

- `flet` 环境是独立 pixi 环境（`no-default-feature`，Python 3.12，Flet 1.1.3），不含 PySide6。旧界面环境仍是 Python 3.11；两者统一到 3.12 是方案 2.3 的单独任务。
- 新的 `test_flet_*.py` 也会被旧环境的 `pixi run test` 发现：依赖 Flet 的用例用 `skipUnless(HAS_FLET)`，令牌与源码检查两边都跑。
- Flet 入口一律用 `app/ui_flet/runner.py` 的 `run(main)` 启动，不直接 `ft.run`：`flet_desktop` 会优先启动 `<当前目录>/build/macos/*.app`、`build/windows/*.exe`，而这里放的是 Nuitka 构建的旧版应用；`run` 会切到中立工作目录（相对的 `IWARATOOL_DATA_DIR` 等先转为绝对路径）。
- Flet 首次启动会从 GitHub 下载桌面客户端（`~/.flet/client`）。本机 Python 内下载可能卡住时，先用 curl 下载 `https://github.com/flet-dev/flet/releases/download/v1.1.3/flet-macos.tar.gz`（Windows 为 `flet-windows.zip`），再设 `FLET_CLIENT_URL=file:///<路径>` 运行一次。

## 提交与 CI

- 在 `feat/flet-ui` 上工作，不直接提交到 `dev` / `main`。
- 每完成一个合适的小单元（能运行、测试通过）就本地提交，**不推送**。
- 关键节点（阶段完成、环境/CI/打包改动、跨平台风险较大的改动）提交并推送，然后观察 Tests 工作流各平台结果（`gh run list --branch feat/flet-ui`、`gh run watch`），失败就修。
- 提交信息沿用仓库风格（英文祈使句标题），不加 AI 署名或 Co-Authored-By。
- 完成后同步更新 `docs/flet_ui_plan.md` 中对应的【需核实】结论与阶段进度。
