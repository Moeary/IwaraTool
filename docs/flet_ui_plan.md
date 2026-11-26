# Flet 前端改造与移植方案（v1.3.0）

编写日期：2026-11-22。本文是 Flet 前端的执行依据，面向实际动手改造的开发者或代理。可行性结论见 [`flet_migration.md`](./flet_migration.md)，本文不重复。

- 第 1 节记录 2026-11-22 对仓库分层和构建流程的核查结论。
- 第 2、3 节是动手写界面前必须完成的工程与核心层整理。
- 第 4～8 节是新界面的架构、设计语言、响应式体系和逐页方案。
- 第 9～11 节是阶段计划、验收方式、新旧对照和待核实事项。

标注约定：**【已核实】** 指已在 Flet 官方文档中确认的接口；**【需核实】** 指文档中没有找到或未读到的能力，实现前必须先写最小样例验证，不能凭印象使用。

---

## 0. 硬性约束

1. 旧版 PySide6 界面 `app/ui/` 与入口 `main.py` 在 Flet 版正式替换前保持可运行、可测试、可发版，不为新界面修改其行为。
2. 新界面全部放在 `app/ui_flet/`，入口为根目录 `main_flet.py`。`app/ui_flet` **禁止**导入 `app.ui`、`PySide6`、`shiboken6`、`qfluentwidgets`、`qframelesswindow`，由测试强制检查（见 C7）。
3. 业务逻辑只写在 `app/core/`（或新增的中立模块）里，界面层只做展示、输入和调度。发现界面需要的逻辑在旧 `app/ui/` 中时，先迁到 core，再让新旧两套界面都调用它，**不得复制一份到 `app/ui_flet`**。
4. 事件处理函数不得在 Flet 事件循环上执行阻塞调用（HTTP、SQLite 批量读写、文件扫描、FFmpeg）。Flet 1.0 起处理函数运行在应用的事件循环上，阻塞会让整个界面卡住。
5. 所有用户可见文字使用 `app.i18n.tr(en, zh, ja)`，与旧界面保持同一套用词。
6. 改动保持小步提交，每步都能运行 `pixi run test`（旧界面）与 `pixi run -e flet test-flet`（新界面）。

---

## 1. 核查结论（2026-11-22）

### 1.1 项目状态

- 分支 `dev`，与 `origin/dev` 同步，领先 `main` 71 个提交；最近一次发版 v1.2.3。
- `plan.md`（本地文件，已被 `.gitignore` 忽略）把 v1.3.0 定为“尝试 Flet 前端”。
- 规模：`app/core` 约 1.98 万行，`app/ui` 约 3.65 万行。

### 1.2 依赖方向：已分离

- `app/core` 中没有任何对 `app.ui` 的导入。
- `app/ui` 以外只有 5 处引用 Qt，均先经过 `qt_available()` 判断：`settings_store.py`、`signal_bus.py`、`logging_setup.py`、`qt_runtime.py`、`core/net_policy.py`。
- `scripts/tests/test_core_without_qt.py` 在拒绝导入 PySide6 的解释器中加载全部核心模块并检查配置、事件、翻译，2026-11-22 运行通过。
- 核心后台线程使用标准库 `threading` / `ThreadPoolExecutor`，不依赖 QThread。

### 1.3 职责划分：未完全分离

以下问题需在写 Flet 页面前处理，对应第 3 节任务编号：

| 编号 | 问题 | 位置 |
| --- | --- | --- |
| C1 | 取消机制只认 QThread：`cancellation_requested()` 只检查 `QThread.isInterruptionRequested()`，普通线程或 `asyncio.to_thread` 中永远返回 False | `app/core/net_policy.py:37`，相关调用约 13 处 |
| C2 | 搜索编排写在 Qt Worker 里：来源与范围分派、Oreno3D 多标签、分级连续扫描 `_scan_rated`、末页计算、输入校验提示 | `app/ui/search/search_worker.py`（377 行） |
| C3 | 其他 Worker 混有业务逻辑：Oreno3D ID/作者解析与并发、首页数据归一化与 `author_user_id`、订阅缩略图与头像并发拉取 | `app/ui/search/oreno_workers.py`、`app/ui/home/workers.py`、`app/ui/subscription/components.py` |
| C4 | 不依赖 Qt 的纯逻辑放在 `app/ui` 下 | 见 3.4 的清单 |
| C5 | 启动与关闭流程绑在 Qt 上；设置写盘依赖 `atexit`，而编译后的 Flet 应用退出时不保证执行 `atexit` | `main.py`、`app/settings_store.py:304` |
| C6 | 事件后端在导入时固定：装了 PySide6 又未设 `IWARATOOL_NO_QT=1` 时会选中 Qt 信号，没有 Qt 事件循环则投递不到；纯 Python 事件在发出线程同步回调 | `app/signal_bus.py` |
| —  | 界面直接引用全局单例：`download_manager` 67 个文件、`app_config` 59 个、`signal_bus` 55 个；界面共调用 `download_manager` 的 91 个方法。不阻塞迁移，Flet 端照 `page_dependencies.py` 的中转模式统一走服务层 | `app/ui/**` |

### 1.4 构建与 pixi：只支持一套界面

- `pixi.toml` 只有一个环境；`start` / `test` / `build` 都默认 Qt。
- `build_windows.py`、`build_macos.py`、CI 的 Linux 步骤都写死 `--enable-plugin=pyside6` 与 `main.py`；`macos_bundle.py` 含 PySide6 嵌套 bundle 修补。这些保留给旧界面。
- 63 个测试文件中 42 个导入 Qt 或 `app.ui`，在没有 PySide6 的环境中 `unittest discover` 会失败。

### 1.5 Flet 打包事实【已核实】

- 当前版本：Flet 1.0 于 2026 年 9 月发布，1.1 于 2026-11-05 发布。
- `flet build` **不会**在项目根目录生成 `android/`、`ios/`、`macos/`、`windows/`、`web/`。它在应用目录下生成并缓存 `build/flutter` 临时工程，产物默认放在 `build/<平台>`；`flet clean` 删除整个 `build/`。
- 原生配置通过 `pyproject.toml` 的 `[tool.flet.*]` 或命令行参数设置：`--android-permissions`、`--android-features`、`--android-meta-data`、`--info-plist`、`--macos-entitlements` 等；更深的定制使用 `--template` 自定义模板。
- 入口模块由 `--module-name` 或 `[tool.flet.app].module` 指定，默认 `main`；应用目录由 `[tool.flet.app].path` 指定。
- 依赖**不读取** pixi/conda 环境，而是按 `[project].dependencies`（或 `requirements.txt`）从 PyPI 与 pypi.flet.dev 下载目标平台的包。
- 可打包的 Python 版本只有 3.12、3.13、3.14，默认 3.14；可用 `--python-version` 或 `requires-python` 选择。
- 默认排除规则只有 `build/`，`exclude` 只做精确匹配、不支持通配符；直接以项目根打包会把 `.pixi/`、`dist/` 等带进去（flet-dev/flet#6839）。
- 编译主机：macOS 可编 macOS/iOS/Android/Web；Windows 可编 Windows（Linux 需 WSL）/Android/Web；Linux 可编 Linux/Android/Web。缺少 Flutter SDK 时会自动下载到 `~/flutter/<版本>`。
- 编译后的 Flet 应用“立即终止”，`atexit` 与 `__del__` 不保证执行。

---

## 2. 工程结构与构建

### 2.1 目录

```
main.py                       # 旧界面入口（不动）
main_flet.py                  # 新入口：第一行设置 IWARATOOL_NO_QT=1，再导入 app
app/
  core/                       # 共用核心
  bootstrap.py                # 新增：两套界面共用的启动/关闭流程（C5）
  presentation/               # 新增：不依赖任何界面框架的展示逻辑（C4）
  ui/                         # PySide6（不动，只改为调用迁出的逻辑）
  ui_flet/
    app.py                    # 根组件、主题、路由、外壳
    layout.py                 # LayoutInfo：尺寸档位、方向、安全区（第 6 节）
    theme.py                  # 设计令牌 → Flet Theme（第 5 节）
    services/                 # 对 core 单例的异步包装与事件桥（4.3）
    stores/                   # 可观察的应用状态
    components/               # 卡片、网格、状态徽章、筛选面板、动作菜单等通用组件
    pages/
      home/ detail/ search/ subscriptions/ author/
      downloads/ library/ rules/ repair/ tools/ settings/
    platform/                 # 桌面与 Android 的差异封装（文件选择、剪贴板、窗口、分享）
packaging/flet/
  pyproject.toml              # 仅供 flet build：依赖、元数据、平台权限
  assets/                     # 图标、启动图
scripts/
  tools/build_flet.py         # 暂存干净源码并调用 flet build
  tests/core/ tests/qt/ tests/flet/
```

`app/ui_flet/pages/<页面>/` 与 `app/ui/<页面>/` 一一对应（资料库对应旧的历史记录），方便新旧对照，见第 10 节。

### 2.2 pixi.toml

拆成共用依赖加两个 feature，两个环境使用同一个 `solve-group`，保证 core 依赖版本完全一致。以下为结构示意，具体版本以实际解出的 lock 为准：

```toml
[workspace]
name = "IwaraTool"
channels = ["conda-forge"]
platforms = ["win-64", "linux-64", "osx-arm64", "osx-64"]

[dependencies]
python = "3.12.*"
requests = ">=2.32.5,<3"

[pypi-dependencies]
cloudscraper = ">=1.2.71"

# ── 旧界面 ──
[feature.qt.dependencies]
nuitka = ">=2.8.9,<3"
zstandard = ">=0.25.0,<0.26"
[feature.qt.pypi-dependencies]
pyside6-fluent-widgets = ">=1.10.2,<2"
[feature.qt.target.win-64.dependencies]
pyside6 = "==6.6.2"
[feature.qt.target.linux-64.dependencies]
pyside6 = "==6.6.2"
[feature.qt.target.osx-arm64.pypi-dependencies]
pyside6 = "==6.6.2"
dmgbuild = ">=1.6"
[feature.qt.target.osx-64.pypi-dependencies]
pyside6 = "==6.6.2"
dmgbuild = ">=1.6"
[feature.qt.tasks]
start = "python main.py"
test = "python -X faulthandler -m unittest discover -s scripts/tests -t . -p \"test_*.py\""   # 跑 core + qt
build = "..."                                       # 维持现有各平台写法

# ── 新界面 ──
[feature.flet.pypi-dependencies]
flet = { version = "==1.1.*", extras = ["all"] }   # 需核实 extras 名称
flet-video = "*"                                    # 解出后固定版本
[feature.flet.tasks]
start-flet = { cmd = "python main_flet.py", env = { IWARATOOL_NO_QT = "1", IWARATOOL_DATA_DIR = "data-flet" } }
test-flet  = { cmd = "python -X faulthandler -m unittest discover -s scripts/tests -t . -p \"test_*.py\"", env = { IWARATOOL_NO_QT = "1", IWARATOOL_TEST_SCOPE = "core,flet" } }
build-flet = "python scripts/tools/build_flet.py"

[environments]
default = { features = ["qt"], solve-group = "main" }
flet    = { features = ["flet"], solve-group = "main" }
```

要点：

- 现有 `[tasks]` 与各平台的 `target.*.tasks`（macOS 测试固定 `QT_QPA_PLATFORM=cocoa`）全部移进 `feature.qt`，不改变旧界面的运行方式。
- `start-flet` 默认使用独立数据目录 `data-flet`（相对路径时按 `app/paths.py` 的规则解析，需确认），避免两套界面争用同一份 INI、SQLite 与下载队列。`data-flet/` 加入 `.gitignore`。
- **已实施**：测试按目录拆成 `scripts/tests/core`（两个环境都跑）、`scripts/tests/qt`（只在 default 环境）、`scripts/tests/flet_ui`（不叫 `flet`：发现测试的顶层是 `scripts/tests`，同名包会遮蔽真正的 flet 库）。`pixi run test` 递归跑全部；`pixi run -e flet test-flet` 跑 core 与 flet_ui。判断依据是在不含 PySide6 的环境中逐个运行，而不是搜索导入语句。
- 原方案：测试的选择方式二选一：按目录拆成 `scripts/tests/{core,qt,flet}`（推荐，清楚），或在 `qt_helpers` 中按 `IWARATOOL_TEST_SCOPE` 跳过。拆目录时注意测试里的相对路径与 `PROJECT_ROOT = parents[2]` 需要随之调整。

### 2.3 Python 3.12

Flet 打包不支持 3.11，因此整个项目升到 3.12：

1. `python = "3.12.*"`，重新解 lock。
2. 旧界面完整跑一遍 `pixi run test` 与 macOS `pixi run build`（含 DMG 冒烟）。PySide6 6.6.2 与 Nuitka 2.8 预期支持 3.12，但必须以实际结果为准；不支持时先升级 PySide6 小版本并单独提交。
3. `packaging/flet/pyproject.toml` 写 `requires-python = ">=3.12,<3.13"`，使打包固定使用 3.12。

> **核查结果（2026-11-25）**：旧界面在 Python 3.12 下 `pixi run test` 全部通过，但 `pixi run build` 产出的 macOS 包冒烟时段错误：Nuitka 2.8.10 编译后导入 shiboken6 6.6.2，崩在 `Shiboken::init → PyType_Ready`（源码运行正常；锁文件中只有 Python 版本变化；清空 `build/macos` 后重建结果相同，同一流程在 3.11 下签名、DMG 与挂载启动全部通过）。按约束 1 不为新界面改动旧界面的 Qt 版本，因此：
> - `default`（旧界面）留在 Python 3.11，`flet` 环境使用 3.12，两者暂不共用 solve-group；核心依赖写在默认 feature 中，两边使用同一份版本范围。
> - 升级 PySide6（6.7 及以上）并让旧界面也升到 3.12 列为独立任务，需要三平台构建与 DMG 冒烟重新验证；完成后再恢复共用 solve-group。

### 2.4 packaging/flet/pyproject.toml

```toml
[project]
name = "iwaratool"
version = "1.3.0"            # build_flet.py 从 app/version.py 写入
requires-python = ">=3.12,<3.13"
dependencies = [
  "flet==1.1.*",
  "flet-video==<固定版本>",
  "requests>=2.32.5,<3",
  "cloudscraper>=1.2.71",
]

[tool.flet]
product = "IwaraTool"
org = "<反向域名，待定>"

[tool.flet.app]
path = "."
module = "main_flet"

# Android 权限、Info.plist、entitlements 等按需添加，名称以 flet build 文档为准
```

### 2.5 scripts/tools/build_flet.py

仿照现有构建脚本，流程：

1. 清空并创建 `build/flet-src/`。
2. 复制 `main_flet.py`、`app/*.py`、`app/core/`、`app/presentation/`、`app/ui_flet/`、`app/data/`、`app/icon.ico`，以及 `packaging/flet/` 下的 `pyproject.toml` 与 `assets/`。**不复制** `app/ui/`。
3. 把版本号写入暂存的 `pyproject.toml`。
4. 在 `build/flet-src/` 中执行 `flet build <平台> --output ../../dist/flet-<平台> --python-version 3.12`。Flet 自己的 `build/flutter` 位于 `build/flet-src/build/`，不会碰到 Nuitka 用的 `build/macos`。
5. 产物重命名为 `IwaraTool-Flet-<平台>-<架构>-<版本>.*`。
6. 冒烟：检查包内不含 `PySide6`、`app/ui/`；桌面平台启动产物并在限定时间内正常退出（可仿照 `main.py --smoke-test` 加一个 `main_flet.py --smoke-test`）。

### 2.6 CI

- `test.yml`：新增 Flet 环境的测试任务（三平台，`pixi run -e flet test-flet`），与现有任务并行。
- `build.yml`：新增独立的 Flet 构建任务，先只在 `workflow_dispatch` 时运行；产物前缀为 `IwaraTool-Flet-*`，**不进入**现有发版的 `files: dist/IwaraTool*` 匹配，直到决定替换旧界面。可以选用官方 `flet-dev/flet-build-action`，但要保证仍通过 `build_flet.py` 暂存源码。
- Android 构建放在 Linux runner 上，签名密钥使用仓库 secrets，没有密钥时只出 debug 包。

【已实施】Flet 构建只在 `workflow_dispatch` 且 `target` 为 `flet` / `all` 时运行（Windows、macOS），产物为 `flet-<OS>-<arch>` 构件。macOS 任务先切到 runner 上最新的 Xcode（≥ 26）：Flet 客户端依赖的 `connectivity_plus` 7.3 使用 macOS 26 SDK 的 `NWPath.isUltraConstrained`，runner 默认的 Xcode 16.4 编译失败。

---

## 3. 核心层整理（写 Flet 页面前完成）

每项单独提交，提交后旧界面测试必须全部通过。

### C1 中立的取消机制（最优先）

- 在 `app/core/` 新增 `cancellation.py`：`CancelToken`（内部 `threading.Event`），以及基于 `contextvars` 的“当前令牌”：`with cancel_scope(token): ...`。
- `net_policy.cancellation_requested()` 改为：先查当前令牌，再查 QThread（保留旧行为）。
- 旧界面的 `ManagedThread.requestInterruption()` 同时设置自身令牌，行为不变。
- 测试：普通线程中设置令牌后，`cancellable_sleep` 与网络请求能在 `CANCEL_POLL_SECONDS` 量级内抛出 `RequestCancelled`；无 Qt 环境同样通过。

【已实施】`app/core/cancellation.py`；`net_policy.cancellation_requested()` 先查令牌再查 QThread。旧界面不必让 `requestInterruption()` 另设令牌：QThread 检查保留，行为不变。

### C2 搜索服务

- 新增 `app/core/search/service.py`：`run_search(filters, scope, page, *, source, cancel) -> SearchPageResult`，包含 `SearchWorker` 中的全部分派逻辑、`_scan_rated`、末页计算和输入校验（校验提示文字保留 `tr()`，或返回错误码由界面翻译，二选一并保持一致）。
- 旧 `SearchWorker.run()` 只剩 `self.result_ready.emit(run_search(...))`。
- 现有 `test_search*.py` 中针对 Worker 的用例改为针对服务，界面测试只验证信号转发。

【已实施】`app/core/search/service.py` 的 `run_search(filters, scope, page, *, source, client, should_stop)`；校验提示保留 `tr()`。旧 Worker 用例原样保留（它们现在经过服务），另有不依赖 Qt 的 `core/test_search_service.py`。

### C3 其余 Worker 的业务逻辑

| 现位置 | 迁往 | 内容 |
| --- | --- | --- |
| `ui/search/oreno_workers.py` 的 `_resolve_oreno_video_id`、`_resolve_oreno_video_details` 和批量解析的并发控制 | `core/search/oreno3d_bridge.py` 或新 `core/search/resolution.py` | 去掉为测试替身写的 `TypeError` 回退，测试替身改为实现完整签名 |
| `ui/home/workers.py` 的 `normalize_items`、`items_from_rows`、`author_user_id`、`FeedWorker._fetch` | `core/home/feed.py` | 首页栏目数据获取函数 `fetch_section(section, *, cancel)` |
| `ui/subscription/components.py` 的刷新、头像、缩略图批量拉取 | `core/subscription/media_cache.py` / `refresh.py` | 带取消与进度回调的批量函数 |
| `ui/search/worker_limits.py` | `core/search/` | 并发上限常量 |

【已实施】`core/home/loading.py`（栏目、详情、评论、私有客户端调用）、`core/search/resolution.py`（入队 ID 与分阶段链接解析）、`core/search/limits.py`、`core/subscription/batch.py`（刷新、头像、封面批量）。为测试替身写的 `TypeError` 回退已删除，缺签名的替身已补全。

### C4 纯逻辑迁出 app/ui

迁到新包 `app/presentation/`（展示用的派生数据与文字，不依赖界面框架），旧位置保留一行 re-export 或直接改引用：

| 现位置 | 内容 |
| --- | --- |
| `ui/search/result_authors.py` | 作者目标解析 |
| `ui/search/history_entries.py`、`ui/search/keyword_language.py` | 搜索历史条目、关键词语言判断 |
| `ui/search/author_navigation.py`、`ui/search/queueing.py` 中与控件无关的部分 | 作者跳转目标、可入队判断 |
| `ui/history/record_state.py` | 记录状态（本地/已移走/远端）与搜索文本 |
| `ui/subscription/item_queries.py` | 订阅条目筛选、排序、可下载 ID |
| `ui/downloads/task_text.py`、`task_statuses.py`、`submitted_urls.py` | 任务文字、状态分组 |
| `ui/downloads/staging_flow.py` 的 `video_payload` | 入队载荷 |
| `ui/window/notification_dispatcher.py` | 通知合并 |
| `ui/media/detail_text.py`、`detail_metrics.py`、`detail_state.py`、`detail_comments.py` 中的纯函数 | 详情页文字与统计格式 |
| `ui/common/formatting.py` | 数字、大小、时长格式 |
| `ui/common/click_dispatch.py` 中的动作常量 | 统一的视频动作定义（见 7.0） |
| `ui/shortcuts/catalog.py` | 快捷键目录：拆成纯数据（动作 ID、默认键位字符串、分组、平台差异）放 `presentation`，`QKeySequence` 解析留在旧界面 |

【已实施】除列表各项外，订阅 `helpers` 的纯函数也迁入 `presentation/subscription/helpers.py`，颜色改为返回语义角色名（`success` / `info` / …），各界面自行映射；返回栈 `presentation/navigation.py` 的页面存活检查可替换（旧界面传入已销毁控件检查）；快捷键目录自带不依赖 Qt 的规范化函数，旧界面安装 `QKeySequence` 版本，二者对所有默认键位的结果由测试保证一致。引用直接改到新模块，不留转发文件（`worker_limits`、`subscription/helpers`、`shortcuts/catalog` 等仍需 Qt 的模块除外）。

### C5 共用启动与关闭

新增 `app/bootstrap.py`，把 `main.py` 中与界面无关的步骤搬过去：

```python
def start(*, frontend: str) -> None:
    # apply_pending_restore → 加载配置 → setup_logging → ensure_temporary_rule
    # → download_manager.apply_config() → restore_cached_login() → start()
    # → background_service.start()

def stop(*, wait: bool, timeout: float = 30.0) -> None:
    # background_service.stop → download_manager.shutdown → 保存任务队列
    # → 关闭数据库 → 同步全部设置存储（替代 atexit）
```

- `IniSettingsStore` 增加公开的 `sync_all_open_stores()`；`stop()` 必须显式调用，不能依赖 `atexit`。
- 旧 `main.py` 改为调用 `bootstrap.start()`，在 `aboutToQuit` 中调用 `bootstrap.stop(wait=False)`，行为与现在一致。
- Flet：桌面窗口设置 `page.window.prevent_close = True`，在 `WindowEventType.CLOSE` 中先 `await asyncio.to_thread(bootstrap.stop, wait=True)` 再 `await page.window.destroy()`【已核实这些接口存在】。Android 在应用切到后台时同步设置与队列（生命周期事件名【需核实】）。

【已实施】`app/bootstrap.py`：`restore_pending_backup()` → `prepare(frontend=)` → `start_services()`，`stop(wait=)` 只执行一次并调用 `settings_store.sync_all_open_stores()`。旧 `main.py` 的步骤顺序不变（`prepare` 仍在建窗口之前、主题之后）。Flet 侧在 `app/ui_flet/lifecycle.py`。顺带修正：`logging_setup` 在 `IWARATOOL_NO_QT` 时不再导入 PySide6。

### C6 事件桥约定

- `main_flet.py` 第一行设置 `os.environ["IWARATOOL_NO_QT"] = "1"`，之后才能导入任何 `app` 模块。
- `app/ui_flet/services/events.py` 订阅 `signal_bus`，回调在后台线程中只做一件事：把事件放进线程安全队列，并通过 `page.run_task`（文档注明可从任意线程调用）或 `loop.call_soon_threadsafe` 唤醒事件循环上的消费者。
- 消费者按类型合并：下载进度按任务 ID 只保留最新值，每 150 ms 批量写入状态；状态变化、错误、完成立即处理。
- 页面卸载时取消订阅；测试断言重复进出页面后订阅数不增长。

【已实施】`app/ui_flet/services/events.py` 的 `EventBridge`（`call_soon_threadsafe`，`coalesce=` 时按键合并、每 150 ms 投递）与页面用的 `SubscriptionGroup`；测试覆盖顺序、合并、协程处理函数、反复进出页面不增加订阅数。

### C7 测试守护

扩展 `test_core_without_qt.py`：

- 遍历 `app.presentation` 与 `app.ui_flet`，在拒绝 Qt 的解释器中全部可导入（`ui_flet` 在 Flet 环境中检查）。
- 静态检查：`app/ui_flet/**` 源码中不得出现 `app.ui`、`from ..ui`、`PySide6`、`qfluentwidgets`。
- `bootstrap.start()` / `stop()` 在临时数据目录中可完整走一遍，`stop()` 后 INI 已写盘。

【已实施】`core/test_core_without_qt.py` 新增 `app.presentation`、`app.bootstrap`（有 Flet 时加 `app.ui_flet`）的无 Qt 导入检查；`core/test_bootstrap.py` 在临时数据目录中完整走一遍 `start` / `stop` 并断言 INI 已写盘。

---

## 4. Flet 前端架构

### 4.1 写法：声明式为主

Flet 1.0 提供声明式写法：`@ft.component`、`ft.use_state` / `use_effect` / `use_context` / `use_memo`，以及 `@ft.observable` 数据类；官方建议较大的应用使用声明式，命令式仍受支持【已核实】。本项目页面多、状态联动多（语言、主题、分级、站点、登录、尺寸档位），采用声明式：

- 根组件 `App` 通过 `ft.run(main)` + `page.render(App)` 挂载。
- 全局状态放在 `stores/` 的可观察数据类中，通过 context 提供，避免层层传参。
- 高频数据（下载进度）先在服务层合并再写入 store，避免每个字节回调都触发重绘。
- 如果某个大列表在声明式下性能不达标（第 9 节的性能验收），只对该组件局部改用命令式，并在代码中注明原因。

### 4.2 分层

```
pages/*  ──读──▶ stores/*  ◀──写── services/*  ──调用──▶ app.core / app.presentation
   │                                   ▲
   └──── 用户操作（async 处理函数）─────┘
```

- **services**：对 core 单例的薄包装，所有阻塞调用写成 `await asyncio.to_thread(...)` 并传入 `CancelToken`。每个页面只从自己的 `services` 入口拿依赖，与旧界面的 `page_dependencies.py` 对应，便于测试替换。
- **stores**：`AppState`（语言、主题、分级、站点、登录状态、当前路由）、`LayoutState`（第 6 节）、`TaskStore`（下载任务）、`SelectionState`（多选）等。
- **components**：只依赖 props 与 store，不直接调用 core。

### 4.3 并发规则

- 处理函数一律 `async def`；耗时工作用 `asyncio.to_thread` 或 `page.run_thread`。
- 每个可取消的操作（搜索、翻页、加载详情、刷新订阅）持有一个 `CancelToken`；发起新请求或离开页面时取消旧令牌，结果回来时校验令牌，过期结果直接丢弃。
- 封面加载使用独立的有界队列（桌面 6～8 并发，移动 4 并发），可见区域优先；复用 `core/image_cache.py` 与订阅封面缓存。

### 4.4 路由

- 使用 `page.route` / `page.push_route` / `page.on_route_change` / `page.views` / `page.on_view_pop`【已核实】。
- 路由表：`/home`、`/home/section/<id>`、`/detail/<kind>/<id>`、`/search`、`/subscriptions`、`/subscriptions/<source_id>`、`/author/<site>/<username>`、`/downloads`、`/downloads/log`、`/library`、`/library/<record_id>`、`/rules`、`/rules/<rule_id>`、`/repair`、`/tools/<tool>`、`/settings`、`/settings/<section>`。
- 保留旧界面的返回语义：一级导航切换不入栈，每个一级页面各自维护返回栈；从别的页面跳转过来的（如搜索结果打开详情、打开作者页），返回回到原页面。逻辑放在 `presentation`，可参考 `ui/window/back_stack.py`。
- 【已实施】返回栈复用 `presentation.navigation.NavigationController`：每个目的地一个 `Destination` 句柄，快照是“该目的地当前路由 + 页面 store 提供的状态”（`app/ui_flet/navigation.py`）；`/detail/…`、`/author/…` 叠在打开它的目的地上；再次点击当前目的地回到其顶层。桌面暂不同步 `page.route`（路由只在 `AppState.path`），移动端接入系统返回时（P8）再同步。
- Android 系统返回键、预测性返回手势如何接入路由【需核实】：文档中 `on_view_pop` 只写了 AppBar 自动返回按钮，需要在真机上确认系统返回是否同样触发。

---

## 5. 设计语言

### 5.1 原则

1. **内容优先**：封面是主角，界面框架安静、低对比，不用大面积描边框把页面切成格子（现有界面卡片描边、面板描边、表头描边叠在一起，视觉噪音偏多）。
2. **一屏一个主操作**：每个视图只有一个强调色主按钮（下载、搜索、添加订阅、保存），其余操作放进次级按钮、图标按钮或“更多”菜单。
3. **渐进展开**：筛选、排序、下载规则等低频设置默认收起，用筛选面板或底部抽屉打开，不常驻占用工具栏。
4. **状态一致**：同一状态在所有页面使用同样的徽章、颜色、文字（见 5.5）。
5. **触控优先，键盘完整**：所有操作都有 48dp 以上的触控目标；桌面端保留全部快捷键与网格键盘导航。

### 5.2 颜色

基于 Material 3 配色体系，以现有品牌青色为种子，保留旧界面的语义色。Flet 主题使用 `color_scheme_seed` 生成后，覆盖以下角色：

| 令牌 | 浅色 | 深色 | 用途 |
| --- | --- | --- | --- |
| primary | `#00848E` | `#4CC2C9` | 主按钮、选中指示、链接 |
| on-primary | `#FFFFFF` | `#00363A` | 主按钮文字 |
| primary-container | `#D9EEF0` | `#2C4B4D` | 选中卡片、选中行、导航指示器 |
| background | `#F6F7F9` | `#111315` | 页面背景 |
| surface | `#FFFFFF` | `#1B1E21` | 卡片、面板 |
| surface-variant | `#EEF1F4` | `#25292D` | 输入框、分段控件底色、骨架屏 |
| outline-variant | `#E1E4E8` | `#33383D` | 必要的分隔线（尽量少用） |
| on-surface | `#1B1B1B` | `#F3F3F3` | 正文 |
| on-surface-variant | `#5F6368` | `#B4B8BC` | 次要文字、元信息 |
| success | `#0F7B0F` / 底 `#DFF3E2` | `#6CCB5F` / 底 `#1E3A22` | 已下载、完成 |
| warning | `#9D5D00` / 底 `#FFF1D6` | `#F0B44C` / 底 `#3D2E12` | 排队、待确认、已移走 |
| danger | `#C42B1C` / 底 `#FDE7E4` | `#FF99A4` / 底 `#47201F` | 失败、删除 |
| info | `#005FB8` / 底 `#E1EEFB` | `#60CDFF` / 底 `#14304A` | 新内容、远端 |

深色模式下的语义色底色为本文给出的建议值，实施时需检查文字对比度不低于 4.5:1。强调色只用于主操作和选中状态，不用于大面积背景。

> **已实施（2026-11-25）**：令牌在 `app/ui_flet/tokens.py`，主题在 `app/ui_flet/theme.py`。与上表的差异：
> - 浅色 primary 改为 `#007A84`（原 `#00848E` 上白字仅 4.48:1、在页面底色上 4.18:1），色相不变；`#00848E` 保留为 `color_scheme_seed`。
> - 补齐 Material 3 表面阶梯：`surface`（页面）→ `lowest` → `card`（surface_container_low）→ `container`（底栏、菜单）→ `raised`（对话框、抽屉）→ `field`（surface_container_highest，输入框、Chip、骨架）。
> - `secondary_container` 设为 primary-container，侧栏/底栏指示器、选中分段、色调按钮共用同一种选中色；`surface_tint` 透明，表面不叠 Material 色调。
> - 新增 inverse 角色供 SnackBar 与 Tooltip 使用，深色模式 inverse_primary 为 `#006B73`。
> - `scripts/tests/test_flet_theme.py` 对全部组合做 WCAG 检查：正文 4.5:1，状态色图标在卡片上 3:1。

### 5.3 字体与字号

- 字体使用系统界面字体，不内置字体文件。日文界面需让日文字形优先于中文字形。
  - **已实施**：主字体不指定（Windows 为 Segoe UI，macOS 为系统字体），所有文字角色设置 `font_family_fallback`，按界面语言排列中日字体（Windows：Microsoft YaHei UI / Yu Gothic UI…；macOS：PingFang SC / Hiragino Sans…）。字体表与旧界面共用 `app/presentation/fonts.py`。用户在设置中选的字体（`ui_font_family`）作为主字体。已在 macOS 上目测日文界面显示日文字形。
  - Flet 主题里没有颜色的 `TextStyle` 会被画成白色（与明暗无关），因此主题中的每个文字样式都显式带颜色，由测试检查。
- 字号（逻辑像素）：

| 角色 | compact | medium 及以上 | 字重 |
| --- | --- | --- | --- |
| 页面标题 | 22（放在顶栏） | 28 | 600 |
| 区块标题（首页栏目、设置分组） | 18 | 20 | 600 |
| 卡片标题 | 14，最多 2 行 | 14，最多 2 行 | 500 |
| 正文 | 14 | 14 | 400 |
| 元信息 | 12 | 12 | 400 |
| 徽章、计数 | 11 | 11 | 600 |

现有页面标题约 40px，过大且占高，新界面下调。
- 系统字体放大到 130% 时不得截断关键文字：卡片文字区不写死高度，按字号计算（见 6.6）。

### 5.4 间距、圆角、阴影、动效

- 间距采用 4 的倍数：4 / 8 / 12 / 16 / 24 / 32。页面左右边距：compact 16（宽度 < 360 时 12），medium 24，expanded 及以上 32。
- 圆角：封面与卡片 12；按钮、输入框 10；徽章 6；底部抽屉顶部 28；对话框 24。
- 阴影：默认不用。桌面端悬停卡片时上移 2dp 并加一层很淡的阴影；触控设备不做悬停效果。
- 动效：150～250 ms，标准缓动；封面载入淡入 180 ms；页面切换使用 Flet 默认过渡。系统开启“减少动态效果”时关闭非必要动画（接口【需核实】）。

### 5.5 状态徽章

所有封面卡片、列表行、详情页使用同一组件 `StatusBadge`：

| 状态 | 文字 | 颜色 | 显示位置 |
| --- | --- | --- | --- |
| 新 | 新 | info | 封面左上 |
| 排队中 / 下载中 | 排队 / 42% | warning / primary | 封面左上；下载中可在封面底部加 3dp 进度条 |
| 本地已下载 | 已下载 | success | 封面左上 |
| 已移走 | 已移走 | warning | 封面左上 |
| 已下载到远端 | 远端 | info | 封面左上 |
| 失败 | 失败 | danger | 封面左上 |
| 未下载 | 不显示 | — | — |

现有搜索卡片逐张写“Not downloaded”，属于无效信息，新界面只在状态值得注意时显示徽章。

### 5.6 图标

使用 Flet 内置 Material Icons，全应用统一，不混用其他图标库。

**已实施**：Flet 1.1 只带传统 Material Icons（每个图标有 filled / `_OUTLINED` / `_ROUNDED` / `_SHARP` 四种字形，没有可变填充的 Material Symbols），因此规则定为：静止状态用线框 `_OUTLINED`，导航选中和开关激活（已点赞、已订阅）用实心 `_ROUNDED`；箭头、关闭等纯线条字形用 `_ROUNDED`。所有图标按含义登记在 `app/ui_flet/icons.py`（`NavIcon` 成对、`StateIcon` 成对、`AppIcon` 单个），页面不得直接写 `ft.Icons.X`，由测试检查。

### 5.7 通用状态视图

每个列表、网格都必须实现四种状态，统一放在 `components/states.py`：

- **加载中**：与真实布局同尺寸的骨架卡片或骨架行，不使用页面中央的大转圈。
- **空**：插图或图标 + 一句说明 + 一个能解决问题的按钮（如“还没有订阅 → 添加作者或播放列表”）。
- **错误**：顶部横幅或内联卡片，写明原因（保留核心层返回的错误文字），附“重试”。
- **未登录**：需要登录的区域显示“登录后查看”与登录按钮，而不是空白。

---

## 6. 响应式布局体系

### 6.1 目标设备与典型尺寸

以下为逻辑像素（dp）下的典型值，用于设计与测试，不代表精确机型参数：

| 设备形态 | 竖向 | 横向 | 宽度档 | 高度档 |
| --- | --- | --- | --- | --- |
| 普通手机 | 360～412 × 780～915 | 780～915 × 360～412 | compact | 横向时 compact |
| 21:9 长屏手机 | 360～411 × 840～960 | 840～960 × 360～411 | compact | 横向时 compact |
| 折叠屏外屏（窄长型） | 约 344～390 × 850～900 | — | compact | — |
| 折叠屏内屏（展开） | 约 673～840 × 840 | 约 840～900 × 673 | medium / expanded | — |
| 平板 | 约 800 × 1280 | 约 1280 × 800 | medium / large | — |
| 大平板 | 约 1024 × 1366 | 约 1366 × 1024 | expanded / large | — |
| 桌面窗口 | 任意，最小 360 × 560 | — | 全部档位 | — |

设计的最小宽度为 **320dp**，在该宽度下不得出现横向溢出。

### 6.2 尺寸档位

采用 Material 3 的窗口尺寸档位，而不是 Flet `ResponsiveRow` 默认的 Bootstrap 断点：

| 宽度档 | 宽度 | 高度档 | 高度 |
| --- | --- | --- | --- |
| compact | < 600 | compact | < 480 |
| medium | 600～839 | medium | 480～899 |
| expanded | 840～1199 | expanded | ≥ 900 |
| large | 1200～1599 | | |
| xlarge | ≥ 1600 | | |

- 由 `app/ui_flet/layout.py` 统一计算 `LayoutState`：`width`、`height`、`width_class`、`height_class`、`orientation`、`aspect`（长边 ÷ 短边）、`safe_insets`、`keyboard_inset`、`is_touch`、`is_mobile`。
- 数据来源：`page.on_resize`（`PageResizeEvent.width/height`）与 `page.on_media_change`（`PageMediaData.padding/view_padding/view_insets/orientation`）【已核实】。启动时首次计算需在页面尺寸可用后进行（`page.width` 在移动端的取值【需核实】）。【桌面已核实】`page.render` 之前 `page.width` 仍是默认的 800×600，设置的窗口尺寸在首次 `on_resize` 才到达（启动时必定触发一次），所以外壳先按当前值渲染、随后由 `on_resize` 修正；`Shell.on_resize` 以一帧（16 ms）合并连续的尺寸事件。
- 组件只读 `LayoutState`，**禁止**在组件中直接读 `page.width` 或写死像素断点。
- 档位切换加 1 帧防抖，避免拖动窗口时反复重建。
- 需要栅格时使用 `ResponsiveRow` 的自定义断点，与上表保持一致【已核实支持自定义】：

```python
BREAKPOINTS = {"compact": 0, "medium": 600, "expanded": 840, "large": 1200, "xlarge": 1600}
ft.ResponsiveRow(breakpoints=BREAKPOINTS, controls=[
    ft.Container(col={"compact": 12, "expanded": 6, "large": 4}, content=...),
])
```

`ResponsiveRow` 依据窗口宽度还是自身宽度计算断点，文档未说明【需核实】；如依据窗口宽度，在双栏布局的子栏中使用时会出错，此时改用 `LayoutState` 计算列数。

### 6.3 应用外壳

| 条件 | 主导航 | 顶栏 | 全局主操作 |
| --- | --- | --- | --- |
| compact 宽且高度非 compact（手机竖屏、折叠屏外屏） | 底部 `NavigationBar`，5 个目的地，始终显示标签 | `AppBar`：页面标题 + 至多 2 个图标 + 头像菜单 | 页面内 FAB（如下载页“粘贴链接”） |
| 高度 compact（手机横屏，含 21:9 横屏） | 收起的 `NavigationRail`，无标签，72dp | 滚动时自动隐藏的精简顶栏 | 侧栏顶部的图标按钮 |
| medium（折叠屏内屏、平板竖屏） | `NavigationRail`，显示标签 | 页面头部（标题 + 操作） | 侧栏顶部“粘贴链接”按钮 |
| expanded | `NavigationRail`，显示标签 | 页面头部 | 同上 |
| large / xlarge | 展开的 `NavigationRail`（`extended=True`，宽 240～256），用户可手动收起，记住选择 | 页面头部 | 同上 |

相关接口：`NavigationRail.extended`、`label_type`、`min_width`、`min_extended_width`，`page.navigation_bar`、`NavigationBar.label_behavior`，`page.drawer` / `page.end_drawer`【已核实】。外壳最外层用 `SafeArea` 处理刘海、挖孔、手势条【已核实】。

#### 目的地重组

旧侧栏有 9 个页面加 4 个底部按钮，手机底栏放不下，按使用频率重组：

| 层级 | 目的地 | 说明 |
| --- | --- | --- |
| 一级（底栏 / 侧栏上部） | 首页、订阅、搜索、下载、资料库 | “资料库”即旧的历史记录，以下载过的作品为中心 |
| 二级（侧栏下部；手机放在头像菜单） | 规则、修复、工具、设置 | 修复、工具仅桌面显示（见 8.3） |
| 全局偏好（头像菜单） | 登录状态与账号、站点 iwara.tv / iwara.ai、内容分级、主题、关于与更新 | 旧界面散落在首页标题栏、侧栏底部的地球、太阳图标中，统一收进这里 |

- 【已实施】头像菜单改为“账号与偏好”面板：`PopupMenuButton` 不能嵌套单选组，铺平后十几项太长，改为分段按钮面板（宽屏对话框，手机底部抽屉），含登录状态、站点、分级、主题、语言与版本；导航放不下的目的地（手机上的二级目的地、高度不足时侧栏的二级组）也列在面板中。宽屏时账号入口在侧栏底部（展开侧栏显示“已登录 / 未登录”），手机在顶栏右侧。
- 【已实施】侧栏高度不足以放下 9 个带标签的目的地时（如 841×673），二级组移入账号面板，避免滚动后才能看到“设置”（`LayoutState.rail_fits`）。底栏使用短标签（英文 Subs / Queue，日文 保存），320dp 下不换行。
- 内容分级与站点是高频切换项，在首页和搜索页顶栏额外放一个**偏好标签**（例如“iwara.tv · 全部”），点击弹出两组单选，与头像菜单中的设置同步。
- 启动页设置（`startup_page`）仍然有效，取值映射到新的目的地。
- 下载页图标上显示进行中任务数徽章，待确认批次存在时显示圆点（对应旧的 `staging_badge`）。

### 6.4 四种页面布局模式

所有页面只使用以下四种模式，便于统一实现和测试：

1. **网格流**（首页、搜索、订阅作品、作者页）：封面网格，列数随宽度变化（6.6）。
2. **列表-详情**（订阅源与作品、规则列表与编辑、设置分类与内容、资料库列表与详情）：宽度 ≥ 840 时左右双栏，左栏 280～360、右栏占满；< 840 时单栏，点击列表项推入详情路由。
3. **单列表单**（设置子页、工具、修复步骤、登录）：内容最大宽度 720，居中；label 与控件在 medium 及以上同一行、在 compact 上下排列。
4. **详情页**（作品详情、播放）：见 7.3。

所有页面内容区在 xlarge 下设最大宽度（网格流 1600，表单 720，列表-详情 1440），两侧留白，避免超宽屏上元素被拉得过远。旧设置页、工具页的控件被推到 1400px 外的最右侧，就是缺少最大宽度的结果。

### 6.5 折叠屏与 21:9

- Flet 文档中没有找到折叠屏铰链与姿态信息（对应 Flutter 的 `MediaQuery.displayFeatures`）【需核实，当前视为不可用】。因此：
  - 只根据宽高档位布局，不做铰链对齐。
  - 列表-详情双栏只在宽度 ≥ 840 时启用。折叠屏展开的竖向内屏（约 673～840 宽）通常落在 medium，使用单栏加更多网格列，内容不会被分在铰链两侧。
  - 折叠/展开时窗口尺寸变化会触发 `on_resize`，必须保证切换后滚动位置、选中项、正在播放的视频、未提交的输入框内容都保持不变：这些状态放在 store 里，不放在组件局部。
- 21:9 长屏竖向：宽度与普通手机相同，档位不变，多出来的高度只用于显示更多行；**不得**按高宽比放大卡片。详情页播放器保持 16:9，下方信息区更长。
- 21:9 横屏（约 900 × 400）：宽度落在 expanded，但高度 compact。此时一律按“高度 compact”处理：收起侧栏、顶栏滚动隐藏、底部抽屉改为右侧面板、播放时默认全屏。判断条件写成 `height_class == "compact"` 优先于宽度档。
- 外屏极窄（约 344dp）：网格保持 2 列（6.6 已按此计算），顶栏图标不超过 2 个，筛选标签横向滚动而不换行。

### 6.6 封面网格尺寸

- 卡片结构：16:9 封面 + 文字区（标题 2 行 + 元信息 1 行）。文字区高度 = 内边距 + 标题行高 × 2 + 元信息行高，随系统字号缩放计算，不写死。
- 不直接依赖 `GridView.max_extent` 推算列数，而是在 `LayoutState` 变化时计算：

```
avail = 内容区宽度 − 左右边距
cols  = clamp(floor((avail + gap) / (min_card + gap)), min_cols, max_cols)
tile_w = (avail − gap × (cols − 1)) / cols
child_aspect_ratio = tile_w / (tile_w × 9 / 16 + text_h)
→ GridView(runs_count=cols, child_aspect_ratio=..., spacing=gap, run_spacing=gap)
```

- 参数：

| 宽度档 | gap | min_card（标准密度） | min_cols | max_cols |
| --- | --- | --- | --- | --- |
| compact | 8（< 360 时）／12 | 150 | 2 | 3 |
| medium | 16 | 180 | 3 | 5 |
| expanded | 16 | 200 | 3 | 6 |
| large / xlarge | 20 | 220 | 4 | 8 |

- 密度：手机与平板提供“紧凑 / 标准 / 大”三档（min_card 乘以 0.85 / 1 / 1.4，“大”在 compact 上即单列大卡）；桌面保留连续的封面大小滑块和 Ctrl+= / Ctrl+- / Ctrl+0，滑块值映射为 min_card（160～420）。两者写入同一个设置项，首页、搜索、订阅、作者页共用。
- 网格使用 `GridView(build_controls_on_demand=True)`【已核实默认开启】，只构建可见卡片；封面图片按可见顺序加载。

### 6.7 交互适配

| 交互 | 触控（手机、平板） | 鼠标键盘（桌面） |
| --- | --- | --- |
| 打开 | 点按 | 单击；Enter |
| 右键菜单 | 长按 → 底部动作面板 | 右键 → 弹出菜单；菜单键、Shift+F10 |
| 多选 | 长按第一项进入选择模式，之后点按切换；顶栏变为上下文操作栏（已选数量、全选、下载、更多） | 悬停显示卡片右上角复选框；Ctrl/⌘ 单击切换，Shift 单击连选；Ctrl+A、Esc |
| 刷新 | 下拉刷新（Flet 是否提供下拉刷新控件【需核实】，不提供则在顶栏放刷新按钮） | F5 / ⌘R |
| 筛选与排序 | 底部抽屉 | expanded 及以上为内联工具栏，或右侧面板 |
| 对话框 | compact：全屏对话框或底部抽屉 | 居中对话框，最大宽度 560 |
| 返回 | 系统返回、屏幕左缘右滑 | 左上角返回、Esc、鼠标侧键、触控板右滑 |
| 提示 | `SnackBar`（`page.show_dialog(ft.SnackBar(...))`【已核实】），破坏性操作附“撤销” | 同左 |

- 触控目标至少 48 × 48dp；桌面可使用较紧凑的视觉密度（行高 40），通过主题的 visual density 切换（接口【需核实】）。
- 键盘弹出时，输入框所在区域通过 `view_insets` 上移，表单底部操作栏不被遮挡（`SafeArea.maintain_bottom_view_padding`【已核实】）。

---

## 7. 逐页方案

每页按“定位 / 旧界面问题 / 新布局 / 交互 / 迁移要点”给出。截图参考 `docs/panel_view/`。

### 7.0 跨页面通用组件

| 组件 | 说明 |
| --- | --- |
| `VideoCard` | 封面、时长（右下）、状态徽章（左上）、选择框（右上）、标题 2 行、元信息 1 行“作者 · 播放 · 点赞 · 日期”。首页、搜索、订阅、作者页、资料库封面视图共用，统一旧版首页卡片与搜索卡片两种不同样式 |
| `VideoGrid` | 6.6 的网格 + 骨架、空、错误状态 + 选择模式 + 键盘导航 |
| `VideoActions` | 统一动作列表，顺序沿用旧版统一右键菜单：查看详情 → 播放（本地 / 远端 / 在线）→ 打开所在文件夹 / 复制文件路径 → 下载 → 在浏览器打开 / 复制链接 → 作者相关 → 本页管理项。同一份定义生成桌面弹出菜单与手机底部动作面板。动作是否可用由 `presentation` 计算 |
| `DownloadSheet` | 点“下载”时弹出：数量摘要、下载规则选择（默认取本页规则）、“记住为本页规则”、跳过已下载开关、确认。替代旧界面每页常驻的规则下拉框 |
| `FilterSheet` | 筛选条件面板，compact 为底部抽屉，宽屏为右侧面板 |
| `SectionHeader` | 区块标题 + 折叠 + 次要信息 + 操作 + “更多 ›” |
| `PageHeader` | medium 及以上的页面头部：标题、副标题、主操作、更多菜单；向下滚动时收缩为单行 |
| `StatusBadge`、`EmptyState`、`ErrorBanner`、`Skeleton` | 见 5.5、5.7 |

### 7.1 首页

- **定位**：发现内容，按栏目浏览。
- **旧界面问题**：标题行同时放封面大小滑块、内容分级分段控件、编辑、全部刷新；栏目标题行同时放分段控件、更新时间、下载当前展示、刷新、更多。宽屏尚可，窄屏必然溢出。
- **新布局**：
  - 顶栏：标题“首页”、偏好标签（站点 · 分级）、更多菜单（自定义栏目、全部刷新、封面大小）。
  - 每个栏目：`SectionHeader`（标题、折叠箭头、“更新于 x 分钟前”、更多菜单：下载当前展示 / 刷新；右侧“更多 ›”），下方一行可横向滚动的排序标签（趋势 / 热度 / 最新，视频 / 图片）。
  - compact：每个栏目是**横向滚动的货架**，卡片宽约 168dp，露出第三张卡片的一部分以提示可滑动；“更多 ›”进入该栏目的完整网格页。
  - medium 及以上：每个栏目显示 2 行网格（列数按 6.6），折叠时只显示标题行。
- **交互**：有新内容时，在栏目上方显示“有新内容，点击显示”标签，不打断用户正在进行的勾选（沿用旧行为）；自定义栏目在 compact 为全屏页面，支持拖动排序（Flet 的可拖动列表控件【需核实】，不可用时提供上移/下移按钮）。
- **迁移要点**：栏目定义与缓存复用 `core/home/feed.py`、`core/home/cache.py`；C3 迁出的 `fetch_section`；布局对话框逻辑参考 `ui/home/layout_dialog.py`。

### 7.2 搜索

- **定位**：按来源、范围、关键词、标签搜索，批量加入下载。
- **旧界面问题**：两行下拉框（来源、范围、排序、分级、筛选、网格/列表），第三行再放分页、下载规则、加入选中、打开选中、回到顶部，工具栏占去约四分之一高度；结果卡片纵向堆 6 行文字，与首页卡片样式不一致。
- **新布局**：
  - 顶部搜索框（`SearchBar` 风格，带搜索历史与标签候选），左侧来源切换（Iwara / Oreno3D，iwara.ai 下 Oreno3D 置灰），右侧清除与重置。
  - 下方一行横向滚动的标签：范围（视频 / 图片 / 作者 / 播放列表 / 标签）+“排序：最新 ▾” +“筛选（2）”。分级不在此重复，使用顶栏偏好标签。
  - 结果摘要行：“找到 22 个视频 · 筛选后 18 个”+ 视图切换（网格 / 列表，桌面）。
  - compact：搜索区随滚动收起，向上滑动时重新出现；结果为 `VideoGrid`；分页改为**滚动到底自动加载下一页**（核心层 `next_page` 已支持，分级扫描时同样从读到的最后一页继续），底部显示“已加载到第 3 页”。
  - expanded 及以上：保留页码与跳页（顶部、底部各一组），兼顾键盘 Alt+← / →；后筛选在右侧面板中展开，不弹窗。
- **交互**：多选后上下文操作栏显示“加入下载（n）”，点后弹出 `DownloadSheet`；作者、播放列表结果使用列表卡片（头像 / 封面 + 名称 + 统计 + 订阅状态）。
- **迁移要点**：C2 的 `run_search`；后筛选 `core/search/post_filter.py`；搜索历史与作者跳转用 C4 迁出的模块；Oreno3D 结果补全后再参与筛选与下载状态判断，沿用旧逻辑。

### 7.3 作品详情与播放

- **定位**：查看单个作品并播放、点赞、下载、跳转作者。
- **新布局**：
  - compact 竖屏：顶部 16:9 播放器（滚动时固定在顶部，可缩小）；下面依次是标题、元信息、操作行（点赞、下载、外部播放、复制链接、更多，均为图标 + 短文字、可横向滚动）、作者卡片（头像、名称、程序内订阅状态、Iwara 关注状态、作者页按钮）、简介（默认 3 行，可展开）、标签、相关推荐（2 列网格）、评论（单独标签页或“查看评论”打开底部抽屉）。
  - 高度 compact（横屏）：播放时进入沉浸全屏；暂停时显示两栏：左播放器、右信息滚动区。
  - expanded 及以上：左侧播放器 + 标题 + 操作 + 作者 + 简介 + 评论，右侧 320～380 宽的相关推荐栏（沿用旧版宽窗口布局）。
  - 图片作品：整组图片竖向排列，点开进入全屏查看器，左右滑动切换。
- **播放器**：使用 `flet-video` 的 `ftv.Video`（基于 media_kit，属性含 `playlist`、`fullscreen`、`controls`、`autoplay`、`volume`、`wakelock`，事件含 `on_error`、`on_position_change`）【已核实】。`VideoMedia` 有 `http_headers` 字段【已核实，flet-video 1.1.3】，但过期地址重签仍需代理，因此**统一通过现有 `core/playback/stream_proxy.py` 的本地回环代理播放远程视频**：代理负责授权头、用户代理设置与过期地址重签，播放器只拿到 `http://127.0.0.1:<port>/s/<token>`。Android 上回环 HTTP 需要允许明文流量（`usesCleartextTraffic` 或网络安全配置），打包时配置并验证。
- **迁移要点**：详情数据与评论来自 C3 迁出的获取函数；文字格式用 C4 迁出的 `detail_*`；点赞、关注接口沿用 `download_manager`；Linux 桌面需完整版客户端才含 Video（`FLET_DESKTOP_FLAVOR=full`）。

### 7.4 订阅

- **定位**：管理作者与播放列表订阅，查看新作品并下载。
- **旧界面问题**：工具栏挤了筛选框、状态下拉、排序、方向、封面滑块、添加、导入、全部刷新共 8 个控件；“总览”与“经典表格”两套视图并存。
- **新布局**：
  - 头部：标题 + 统计（订阅 6 · 新 48 · 未下载 32）；主操作“添加”（compact 为 FAB）；更多菜单：导入已关注、全部刷新、缓存全部封面。
  - 工具行：搜索框 + “状态：全部 ▾” +“排序：名称 ↑ ▾”。
  - compact / medium：总览列表，每个订阅一张卡片：头像、名称、类型、“8 新”徽章、刷新与更多按钮，下方横向滚动的最近作品货架；点卡片进入该订阅的作品网格页。
  - expanded 及以上：**列表-详情双栏**：左栏订阅列表（头像、名称、新增数、未下载数），右栏选中订阅的作品网格与筛选。双栏取代旧的“经典表格”，不再保留第二套视图。
- **交互**：进入某个订阅时，该页封面排到封面队列最前（沿用旧行为）；作品网格支持状态筛选、标题搜索、多选下载（`DownloadSheet`）、标记为已处理。
- **迁移要点**：`core/subscription/*`；C3 迁出的批量刷新与封面拉取；C4 迁出的 `item_queries`；订阅源按站点过滤的规则不变。

### 7.5 作者页

- 与订阅作品网格共用布局：头部为作者卡片（头像、名称、两枚状态标签“程序内订阅 / Iwara 关注”、订阅与关注按钮），下方“视频 / 图片”标签与 `VideoGrid`，支持翻页与多选下载。已在程序内订阅的作者直接显示订阅网格（沿用旧行为）。

### 7.6 下载

- **定位**：提交链接、查看与控制下载任务、处理待确认批次。
- **旧界面问题**：左侧常驻“目标链接”与“运行日志”两块面板，占去四分之一宽度；任务中心头部 7 个按钮加 3 个下拉；表格列被截断，状态列只显示“Do...”“Qu...”；统计数字是一行纯文本。
- **新布局**：
  - 链接栏：输入框（支持粘贴多条链接）+ 粘贴按钮 + “下载”主按钮；规则在点击下载后的 `DownloadSheet` 中选择。compact 下链接栏收为 FAB“粘贴链接”，点后弹出底部抽屉，自动读取剪贴板。
  - 头部右侧：总下载速度、登录状态。
  - 待确认批次：存在时在任务列表上方显示提示卡片“2 个批次待确认 → 查看”，进入后显示示例文件名、换规则、确认（复用旧 `staging_panel` 的流程）。
  - 状态标签（带计数，可横向滚动）：全部 · 下载中 3 · 排队 2 · 失败 0 · 已完成 · 已跳过 · 已取消。替代旧的状态下拉和统计文字。
  - 任务行（所有档位）：小封面、标题、作者、进度条、“21.2 / 48.0 MB · 4.2 MB/s · 剩余 6 秒”、状态徽章、行尾操作（暂停 / 继续 / 重试 / 更多）。compact 下为两行布局，medium 及以上为单行。不再使用横向滚动的宽表格。
  - 批量操作放进更多菜单：重试失败、全部取消、清除已完成、恢复全部、优先级设置；多选后上下文操作栏显示“调整优先级”“取消”“移除”。
  - 运行日志：独立路由 `/downloads/log`（compact）或可折叠的底部面板（expanded 及以上），默认收起，有错误时在头部显示红点。
  - “跳过已下载”开关移到 `DownloadSheet` 与设置中。
- **迁移要点**：任务状态来自 `TaskStore`（C6 的事件桥 + 150 ms 合并）；排序与筛选用 C4 迁出的 `task_statuses`、`task_text`；诊断“为什么没下载”复用 `download_manager.explain_not_downloaded`，以底部抽屉显示结果。

### 7.7 资料库（原历史记录）

- **定位**：浏览、查找、打开已下载的作品，清理失效记录，导出。
- **旧界面问题**：表格列多且窄，质量、点赞、播放、日期大量被截断；顶部常驻显示数据库路径；“全部 / 最近下载 / 未观看 / 按作者”与“列表 / 网格 / 封面”两组分段控件并排。
- **新布局**：
  - 头部：标题 + 统计（记录 25 · 正常 25 · 已移走 0 · 未观看 25）；更多菜单：清理失效记录、同步下载文件夹、导出 CSV、列设置。数据库路径移到“设置 → 系统与维护”。
  - 工具行：搜索框（可选搜索字段）+ 范围标签（全部 / 最近下载 / 未观看 / 按作者）+ 状态筛选 + 视图切换（列表 / 封面）。
  - compact：列表行 = 小封面 + 标题 + “作者 · 下载日期 · 大小”，状态正常时不显示徽章；“按作者”使用分组标题。点行进入详情路由（大封面、文件路径、质量、统计、操作：播放、打开文件夹、复制路径、浏览器打开、删除记录）。
  - expanded：表格视图，列宽按内容设定（标题自适应，数字列右对齐并格式化为 1.2K、67.2K），可选列；large 及以上增加右侧详情栏，选中行即显示。
  - 封面视图：`VideoGrid`。
- **交互**：删除记录使用 SnackBar 撤销，而不是确认对话框；1 万条记录时滚动流畅（第 9 节性能验收）。
- **迁移要点**：Flet `DataTable` 不是按需构建的，大数据量下使用 `ListView` + 自定义行组件 + 固定表头实现表格视图（`ListView` 是否按需构建【需核实】，不行则分页加载）；记录状态用 C4 迁出的 `record_state`；CSV 导出逻辑若在 `ui/history/csv_export.py` 中，先迁到 core。

### 7.8 下载规则

- **定位**：编辑文件名模板、筛选条件和下载行为，设置各页面使用的规则。
- **旧界面问题**：页面顶部常驻一行 5 个“各页面使用的规则”下拉框；模板编辑器每行“字段下拉 + 分隔符 + 删除”，只显示完整模板，不显示示例文件名。
- **新布局**：
  - 列表-详情：左栏规则列表（名称、默认标记 ●、摘要“视频 · 无筛选”、“用于：订阅、首页”），底部“新建”“复制”；右栏编辑器。
  - “各页面使用的规则”收进一个可折叠卡片，放在规则列表上方；compact 下作为单独入口。
  - 编辑器分组：基本（名称、类型）→ 文件名（**醒目的示例文件名预览**，如 `aoi_motion/2026-11-14_Starlight Dance_abc123.mp4`，下方为可拖动排序的字段片段与分隔符，以及可编辑的完整模板和校验）→ 筛选 → 行为（是否写入历史等）。
  - 底部固定操作栏：设为默认、保存；有未保存修改时离开页面先询问。
- **迁移要点**：`core/rules.py`、`core/download/template_builder.py`；旧界面的 `ui/rules/template_builder.py` 中若有字段拼装逻辑，先迁到 core。

### 7.9 修复中心（桌面）

- 改为步骤式流程：① 选择文件夹 → ② 规则与选项（原地重命名 / 整理到输出目录，封面、NFO、写入历史）→ ③ 扫描与预览（原路径 → 目标路径，冲突项标注并默认跳过）→ ④ 执行（进度与日志）→ ⑤ 结果摘要。expanded 及以上用横向步骤条，compact 用纵向步骤。
- 复用 `core/repair/*`，Worker 中的循环与批量刷新逻辑（`ui/repair/worker.py`）迁到 core 并支持 C1 的取消。
- Android 默认不显示（见 8.3）。
- 【已实现】`pages/repair/page.py` + `stores/repair.py`；循环与批量回调在 `core/repair/runs.py`（`should_stop` 默认跟随取消令牌），表单、行状态、日志文字在 `presentation/repair/`，旧页面改为调用。步骤条：宽屏横向、窄屏只标当前步骤（纵向步骤在手机上太占高度，改为可横向滚动的一行）。扫描后改选项会重新规划目标路径（150 ms 去抖、后台线程）。

### 7.10 工具（桌面）

- 左侧工具列表（上传压缩、封面预览），右侧单列表单，最大宽度 720，label 与控件同一行，避免控件被推到屏幕最右侧。
- 上传压缩：源文件区域支持拖放（【已核实】Flet 1.1.3 不能接收系统拖入的文件，只保留“浏览”与输入路径）；显示判断结果卡片（可直接上传 / 只需转封装 / 需要重新编码及目标码率）；压缩中显示进度与剩余时间，可取消。
- 封面预览：16 张截图的网格，选中后保存或复制时间码。
- 复用 `core/tools/*`；未找到 ffmpeg 时显示安装指引空状态。
- 【已实现】`pages/tools/page.py` + `stores/tools.py`；工具列表、判断卡片、选项保存、进度与封面文字在 `presentation/tools/`（`common`、`compress`、`covers`、`texts`），旧工具页改为调用。输出、开始、进度合为一张卡片放在判断卡片下方，设置卡片在后；设置页改 ffmpeg 路径后工具页重新检测。

### 7.11 设置

- **旧界面问题**：语言等部分设置立即生效，但底部又有“保存全部设置”按钮，行为不一致。
- **新布局**：列表-详情（expanded 及以上）/ 分类列表 → 子页面（compact，Android 设置的常见做法）。分类沿用旧版：通用、首页、账号与网络、下载、搜索、订阅与刷新、工具、系统与维护、键盘快捷键（快捷键与工具仅桌面）。
- **保存方式**：每项修改立即保存并生效，去掉“保存全部设置”；需要校验的项（路径、aria2 地址、代理）在失焦或点“应用”时校验，失败时就地显示错误，不写入。
- 内容最大宽度 720；每组为一张卡片，组内为“标题 + 说明 + 控件”的行。
- 复用 `app/config.py` 的设置项；旧 `ui/settings/persistence.py` 中的转换逻辑若有，迁到 core。

### 7.12 账号与全局偏好

- 头像菜单（所有档位都在顶栏或侧栏底部）：登录状态 / 登录 / 退出、站点切换、内容分级、主题（跟随系统 / 浅色 / 深色）、设置、关于与检查更新。
- 登录使用单列表单页；登录失败原因就地显示。

---

## 8. 平台差异

### 8.1 桌面

- 窗口：记住尺寸与位置；最小尺寸 360 × 560（`page.window` 的最小尺寸属性名【需核实】）。
- 关闭：`prevent_close` + 关闭事件中走 `bootstrap.stop()`（C5）。旧版的“关闭到托盘继续下载”在 Flet 中没有托盘支持【需核实，视为不可用】，改为关闭时若有进行中的任务弹出确认：“仍有 3 个任务在下载：最小化 / 退出”。
- 快捷键：使用页面键盘事件（`page.on_keyboard_event`【需核实名称】）实现，键位数据来自 C4 拆出的中立快捷键目录，macOS 显示 ⌘ ⌥ ⌃ ⇧。全局快捷键（应用不在前台时）不支持。
- 系统通知：Flet 未提供【需核实】，暂用应用内 SnackBar 与下载页徽章代替。

### 8.2 Android

- 数据目录：应用私有存储（Flet 打包环境变量或存储路径接口【需核实】），在 `main_flet.py` 中设置 `IWARATOOL_DATA_DIR`；下载目录默认放在应用专属外部目录，用户可通过目录选择器另选（分区存储限制下的可行性【需核实】）。
- 后台：切到后台后 Python 进程可能被挂起，下载会中断。第一版只保证前台下载并启用常亮（`Video.wakelock` 之外，下载页需要单独的常亮能力【需核实】），切回前台时自动继续；前台服务作为后续工作。
- **远端 aria2 是 Android 上最有价值的下载方式**：手机只负责浏览和提交，文件由 NAS 下载，规避后台与存储限制。设置向导中突出这一选项。
- 分享：接收浏览器“分享到 IwaraTool”的链接（Android intent）【需核实】，可行时直接打开 `DownloadSheet`。
- 剪贴板、文件选择、外部打开链接使用 Flet 提供的接口，封装在 `ui_flet/platform/`。

【已实现（P8，真机验收待做）】
- 数据目录：打包后 `runner.prepare_environment` 取 `FLET_APP_STORAGE_DATA/IwaraTool`（与桌面同一路径）。下载目录在用户未自选时，启动时由 `platform/storage.py` 改为 `StoragePaths.get_external_storage_directory()/Downloads`（即 `Android/data/<包名>/files/Downloads`，无需权限、文件管理器可见、卸载时随应用删除），取不到时退回数据目录下的 `Downloads`。
- 生命周期：`lifecycle.install_pause_handler` 在 `pause` / `hide` / `detach` 时调用 `bootstrap.checkpoint()`（保存队列与设置，不停止服务）；手机没有关窗流程。
- 常亮：`Shell.refresh_badges` 在有下载进行时 `ft.Wakelock().enable()`，结束后关闭（`wakelock_plus`，Flet 核心自带，无需权限）。
- 返回键：根视图 `can_pop=False` + `on_confirm_pop` 调用 `Shell.back()`，能后退就留在应用内，否则 `confirm_pop(True)` 退出；对话框与面板由 Flutter 先行关闭。手机在其他顶层目的地按返回先回首页（Material 惯例）。
- 远端 aria2：手机隐藏“aria2 在另一台设备”开关并固定为远端，三个远端路径字段直接显示，卡片顶部说明“手机只负责浏览和提交”。
- 分享：Flet 1.1.3 只有对外分享（`ft.Share`），没有接收 intent 的接口【已核实】。替代为：应用回到前台时读取剪贴板，若是新复制的 Iwara 作品 / 作者链接，弹出提示条“打开”（`presentation/clipboard_links.py`，每次复制只提示一次）。
- 明文回环：`packaging/flet/pyproject.toml` 的 `[tool.flet.android.manifest_application] usesCleartextTraffic = true`（流代理在 `http://127.0.0.1`）。
- 打包：`build_flet.py --platform apk`（递归审计 APK 内嵌的 `app.zip`，`--build-number` 由版本号得出，如 1.3.0 → 10300）；CI `build.yml` 的 `build-flet-android` 任务在 Ubuntu 上构建并上传 APK（按需触发）。
- 开发：`main_flet.py --platform android`（`flet_viewports.py` 同名参数）让桌面运行按手机能力隐藏功能，便于截图检查。
- 模拟器冒烟：CI `android-emulator` 任务（API 34，x86_64，手机 pixel_6、21:9 手机（pixel_6 改为 1080×2520）、折叠屏 pixel_fold 展开与合上（外屏 1080×2092）、平板 pixel_tablet）用 `scripts/tools/android_smoke.sh` 安装 APK、启动 90 秒，进程存活且 logcat 无 Python traceback / FATAL EXCEPTION 才通过，并截图、检查首页按返回键离开应用。首次运行即发现 APK 启动崩溃：cloudscraper 按文件路径读 `browsers.json`，Android 上纯 Python 包在 zip 内读不到，已用 `[tool.flet.android] extract_packages = ["cloudscraper"]` 修正；五种形态均能启动并显示首页（手机、21:9、折叠屏外屏为底栏，折叠屏内屏为导航栏，平板为侧栏），首页按返回键回到系统桌面。模拟器不替代真机验收（下载、播放、存储、返回手势仍待真机）。

### 8.3 功能可用性

| 功能 | 桌面 | Android | 说明 |
| --- | --- | --- | --- |
| 首页、搜索、详情、作者页、订阅 | ✅ | ✅ | |
| 内置下载器 | ✅ | ⚠️ 仅前台 | 见 8.2 |
| 远端 aria2 | ✅ | ✅ | Android 推荐 |
| 本机 aria2 | ✅ | ❌ | |
| 资料库 | ✅ | ✅ | 打开文件夹在 Android 上不提供 |
| 下载规则 | ✅ | ✅ | |
| 修复中心 | ✅ | ❌ | 需要遍历任意文件夹 |
| 工具（FFmpeg） | ✅ | ❌ | Android 无 ffmpeg 可执行文件 |
| 自定义外部播放器命令 | ✅ | ❌ | |
| 开机自启、托盘、快捷键 | ⚠️ 自启保留，托盘不可用 | ❌ | |

不可用的功能在导航与设置中直接隐藏，而不是显示后报错。可用性判断集中在 `ui_flet/platform/capabilities.py`。

【已实现】`capabilities(shell.state.layout)`（`Shell.caps`）给出 `repair`、`tools`、`open_folder`、`local_aria2`、`player_command`、`keyboard_shortcuts`、`window`、`keep_awake`。手机上：导航与路由不含修复 / 工具（`routes.allowed`，深链接与 `--route` 也回到首页）；视频动作菜单、任务菜单、设置的“打开数据 / 下载 / 日志文件夹”隐藏；播放器选项去掉“自定义命令”；“已完成任务单击行为”隐藏（手机点已完成任务打开资料库记录）；窗口卡片只保留启动页。

---

## 9. 阶段计划与验收

每个阶段结束时提交一次，并附上第 9.2 节要求的截图到 `docs/verification/flet/<阶段>/`。

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| P0 工程 | Python 3.12；pixi 双环境；测试分层；`main_flet.py` 空壳；`build_flet.py`；CI 测试任务 | 旧界面测试与 macOS 构建通过；`pixi run -e flet start-flet` 打开空白外壳；Flet 包内不含 PySide6 |
| P1 核心整理 | 第 3 节 C1～C7 | 旧界面全部测试通过；无 Qt 测试覆盖新模块；`app/ui` 中不再有 3.4 清单里的逻辑 |
| P2 外壳 | 主题、`LayoutState`、导航外壳、路由与返回栈、i18n、头像菜单、事件桥、关闭流程 | 9.2 全部尺寸下无溢出；拖动窗口跨档位时导航正确切换；关闭后设置已写盘 |
| P3 搜索与下载闭环 | 搜索页、`VideoGrid`、`DownloadSheet`、下载页、任务事件 | 搜索翻页、横竖屏筛选、单项与批量下载、进度、取消、失败重试、跳转资料库定位；运行时未加载 PySide6 |
| P4 首页、详情、播放 | 首页货架与网格、详情页、播放器、流代理 | 本地与远程播放、跳转、全屏、过期地址续播；32 / 100 张封面滚动流畅 |
| P5 订阅与作者 | 订阅总览与双栏、作者页 | 刷新、筛选、批量下载、封面优先级 |
| P6 资料库、规则、设置 | 资料库、规则页、设置页（含自定义首页与快捷键） | 1 万条记录滚动与搜索；规则编辑与示例预览；设置即时生效 |
| P7 修复与工具 | 仅桌面 | 与旧界面同一组样本结果一致（已完成，两边共用 `core/repair/runs.py` 与压缩计划，见 `docs/verification/flet/P7/`） |
| P8 Android | 打包、权限、数据目录、远端 aria2、明文回环、返回键 | 真机：普通手机、21:9 手机、折叠屏内外屏、平板，各完成一次浏览→下载→播放（代码、CI 打包与三种模拟器启动冒烟已完成，见 `docs/verification/flet/P8/`；**真机验收待做**） |
| P9 对比与决策 | 按 `flet_migration.md` 的性能对比项与旧界面对比 | 形成结论：是否替换旧界面及剩余差距（已完成：桌面功能已齐、性能可接受，Flet 可替换旧界面；正式替换前需手动试用打包版的托盘、开机自启、系统通知，并完成 Android 真机验收，由维护者决定发布方式，见 `docs/verification/flet/P9/`） |

### 9.1 测试

- 核心层：现有单元测试继续使用。
- `presentation`：纯函数单元测试。
- `ui_flet`：`LayoutState` 档位计算、网格列数与宽高比计算、返回栈、事件合并、`VideoActions` 可用性等写成不依赖界面的单元测试；页面级测试以服务替身驱动组件，断言渲染出的关键控件（Flet 测试工具【需核实】）。

### 9.2 尺寸验收清单

`main_flet.py` 增加开发参数 `--viewport WxH`，在桌面上以指定窗口尺寸启动，用于模拟：

| 尺寸 | 模拟对象 |
| --- | --- |
| 320 × 640 | 最小宽度 |
| 344 × 882 | 折叠屏外屏（窄长型） |
| 360 × 800 | 普通手机 |
| 411 × 960 | 21:9 手机竖屏 |
| 960 × 411 | 21:9 手机横屏（高度 compact） |
| 673 × 841 | 折叠屏内屏竖向 |
| 841 × 673 | 折叠屏内屏横向 |
| 800 × 1280 | 平板竖屏 |
| 1280 × 800 | 平板横屏 |
| 1440 × 900 | 桌面 |
| 2560 × 1440 | 大屏 |

每个页面在以上尺寸下检查：无横向溢出、无文字截断导致信息丢失、主操作可见、导航形态符合 6.3、系统字体 130% 时仍可用。

---

## 10. 新旧对照

| 页面 | 旧界面 | 新界面 | 主要变化 |
| --- | --- | --- | --- |
| 外壳 | `ui/main_window.py`、`ui/window/*` | `ui_flet/app.py`、`layout.py` | 底栏 / 侧栏自适应；全局偏好收进头像菜单 |
| 首页 | `ui/home/*` | `pages/home/` | compact 改为货架；栏目头部精简 |
| 详情 / 播放 | `ui/media/*`、`ui/player/*` | `pages/detail/` | flet-video + 流代理 |
| 搜索 | `ui/search/*` | `pages/search/` | 搜索栏 + 标签；手机自动加载下一页；规则移入下载面板 |
| 订阅 | `ui/subscription/*` | `pages/subscriptions/` | 宽屏双栏取代经典表格 |
| 作者 | `ui/author/*` | `pages/author/` | 与订阅网格共用布局 |
| 下载 | `ui/downloads/*` | `pages/downloads/` | 任务行取代宽表格；日志默认收起；状态标签带计数 |
| 资料库 | `ui/history/*` | `pages/library/` | 手机列表行 + 详情页；宽屏详情栏 |
| 规则 | `ui/rules/*` | `pages/rules/` | 示例文件名预览；各页面规则收进折叠卡片 |
| 修复 | `ui/repair/*` | `pages/repair/` | 步骤式流程，仅桌面 |
| 工具 | `ui/tools/*` | `pages/tools/` | 限制表单宽度，仅桌面 |
| 设置 | `ui/settings/*` | `pages/settings/` | 即时保存；手机分类 → 子页 |
| 快捷键 | `ui/shortcuts/*` | `ui_flet/shortcuts.py` + `presentation` 目录数据 | 仅桌面 |

功能取舍（实施中有新的取舍时补进本表）：

| 旧功能 | 处理 |
| --- | --- |
| 订阅页“经典表格” | 移除，由宽屏双栏代替 |
| 每页常驻的规则下拉框 | 移入 `DownloadSheet`，页面规则设置保留 |
| 设置页“保存全部设置” | 移除，改为即时保存 |
| 托盘、关闭到托盘 | Windows：`platform/tray_windows.py`（ctypes `Shell_NotifyIconW`，独立线程的隐藏窗口与消息循环，单击显示、右键“显示 / 退出”）；macOS：关闭时最小化到程序坞。均由“关闭窗口时继续运行”（`minimize_to_tray`）控制，关闭时为关闭确认 |
| 系统通知 | `core/system_notify.py`（macOS `osascript`、Windows PowerShell 吐司、Linux `notify-send`）；窗口在前台时为应用内提示，在后台时为系统通知（受“桌面通知”开关控制，失败时退回应用内提示）。完成 / 失败下载合并为一条，文字与旧版共用 `presentation/notifications.notification_message` |
| 双指右滑返回（macOS） | 依 Flet 能力，【需核实】 |
| 头像下拉菜单 | 改为“账号与偏好”面板（分段按钮），见 6.3 |
| 鼠标侧键返回、多段快捷键（如 Ctrl+K, Ctrl+S） | Flet 键盘事件暂未接入鼠标侧键；多段快捷键暂不匹配。Esc、Alt+← 返回可用 |
| 搜索页网格 / 列表（表格）视图切换 | 先只有网格；列表视图与列显示设置放到 P6 与资料库表格一起做 |
| 搜索框下拉的历史与标签候选 | 历史改为页头“搜索记录”菜单；标签候选（P4 补齐）在“标签搜索”范围下以搜索框下方的一行标签显示，点选后补全（共用 `presentation/search/tag_completion.py`） |
| 搜索结果右键菜单（统一动作列表） | P4 完成 `VideoActions`（`presentation/media/video_actions.py`，旧界面菜单共用文字与位置判断）：桌面右键弹出菜单，手机长按打开动作面板（含“选择”，选择模式下长按仍为勾选）。首页、搜索、首页完整网格、详情页相关作品共用 |
| 旧版独立播放窗口与“弹出播放” | 改为详情页内嵌播放器 + flet-video 自带全屏；菜单“播放”打开详情并直接播放；系统 / 自定义播放器设置对本地副本仍生效 |
| 详情页作者卡的程序内订阅与 Iwara 关注状态 | P5 完成：`AuthorStatusBar` 的文字与资料解析迁入 `presentation/author/status.py`，Flet 用 `components/author_status.py`（作者页、订阅详情栏、详情页作者卡共用），订阅增删后自动同步 |
| 图片作品点开全屏查看器、左右滑动 | 暂沿用旧行为：点按在浏览器打开原图；查看器放到 P8 移动端 |
| 首页“自定义栏目”、封面大小滑块 | 更多菜单入口指向设置的首页分区，P6 设置页实现；封面大小随 6.6 档位自动 |
| 首页栏目“下载当前展示” | 移入栏目更多菜单，经 `DownloadSheet` 选择规则 |
| 搜索页按作者浏览（作者卡片 → 该作者视频）、作者订阅 | 作者卡片打开 `/author/<用户名>`（P5 作者页） |
| 下载页任务表格的列、排序、多选批量 | 改为任务行 + 状态标签 + 关键词筛选；批量动作在页头“更多”（重试失败、全部取消、恢复已取消、清除已完成）；按行多选暂缺 |
| 下载规则的每页常驻选择 | `DownloadSheet` 里选择并可“记住为本页规则” |
| 订阅页源列表的列设置、排序方向按钮 | 源列表改为头像 + 名称 + 未下载数 + 新增角标的行；排序为单选菜单（每种排序自带方向：数量、时间类倒序，名称正序） |
| 订阅源右键“在搜索页查看作品” | 作者订阅改为“作者主页”（`/author/<用户名>`，视频 / 图片标签页），Flet 版不再有按作者搜索的入口 |
| 订阅作品区“历史操作”“下载操作”两个下拉按钮 | 主按钮“下载当前 / 下载选中”（经 `DownloadSheet` 选规则），其余（下载新增、全选、标为已看、标为已移走、还原已移走）收进“…”菜单 |
| 订阅页手机端的“源列表 / 作品”切换按钮 | 手机为总览卡片（每个源一行最近作品货架），点卡片进入 `/subscriptions/<id>`；宽屏（`two_pane`）为左右双栏 |
| 后台自动刷新订阅的系统通知 | 暂以应用内提示显示，并重新读取订阅列表 |
| 资料库“列表 / 网格 / 封面”三种视图与封面大小滑块 | 合并为“列表 / 封面”两种：列表在手机为缩略图行、宽屏为表格；封面视图用 `VideoGrid`，大小随 6.6 档位 |
| 资料库表格的列设置、拖动列宽 | 改为按可用宽度自动取舍列（标题至少 240，窄时依次隐藏画质、发布日期、点赞、播放、作者）；点表头排序 |
| 资料库删除记录前的确认框 | 改为立即从列表移除 + 提示条“撤销”（6 秒内可撤销，之后或关窗时才写库）；“清理已移走记录”仍先确认 |
| 资料库表格行尾的“页面 / 文件夹 / 文件 / 重命名 / 删记录”按钮列 | 收进记录详情（宽屏为右侧栏，手机为 `/library/<id>` 页），动作来自共用的 `VideoActions`，另加重命名与删除记录 |
| 资料库顶部常驻的数据库路径 | 移到设置的“系统与维护” |
| 规则编辑器的“字段下拉 + 分隔符 + 删除”逐行模板构建器 | 改为醒目的示例文件名 + 可编辑的完整模板 + “预设”菜单 + “插入字段”菜单（自动补分隔符）+ 检验；解析与预览仍用 `core/download/template_builder.py` |
| 规则页顶部常驻的 5 个“各页面使用的规则”下拉框 | 收进可折叠的“各页面使用的规则”卡片；选择经 `rule_editing.choose_page_rule`（与 `DownloadSheet` 的“记住为本页规则”同一入口） |
| 规则标签输入框的中英文标签候选弹窗 | 改为输入框下方的一行候选标签（150 ms 去抖、后台查词典），点选后补全并开始下一个；补全规则 `rule_editing.tag_fragment / apply_tag` 两边共用 |
| 切换规则时丢弃未保存的修改 | 有未保存修改时先询问“放弃修改” |
| 设置页底部“保存全部设置”，部分项又立即生效 | 全部即时保存；文本 / 数字在失焦或回车时保存并回显夹取后的值；代理需点“应用”（两个开关与地址一起生效）。文案、分类、夹取与副作用迁到 `presentation/settings/`（`texts`、`catalog`、`values`），旧设置页改为调用 |
| 设置分类为左侧列表 + 右侧堆叠页 | 宽屏同为列表-详情；手机为分类列表 → `/settings/<分类>` 子页；旧链接 `/settings/account` 等按别名进入所属分类 |
| 字体选择对话框（枚举系统字体） | Flet 无法枚举系统字体：改为输入字体名 + “恢复自动”，并显示实际生效的字体顺序 |
| 首页“自定义栏目”两个对话框 | 改为 `/settings/home/rows` 一页：列表（勾选、上下移、编辑、删除）+ 同页编辑表单，保存后首页重载一次；规则迁到 `presentation/home/layout.py` |
| 快捷键录制按钮（QKeySequence） | 点击后由外壳接管下一次按键（`Shell.key_capture`），Esc 取消、Backspace 清除；显示用 `presentation.shortcuts.native_text`（macOS 为 ⌘⌥⌃⇧）；绑定 / 恢复 / 冲突提示两边共用 |
| 开机自启、关闭到托盘 | `core/login_items.py`（与旧版 `core/autostart.py` 分开、条目名不同，互不覆盖）：Windows 注册表 Run 值 `IwaraTool-Flet`、macOS LaunchAgent、Linux XDG autostart；打包后启动应用本身，源码运行启动解释器。托盘见上 |
| 新版本提示（主窗口确认框） | 外壳监听 `release_update_available`，确认后打开 Release 页；判断与文案在 `presentation/updates.py`，两边共用 |
| 修复中心的单页表单 + 七列表格（双击 ID 格修改） | 改为五步流程（文件夹 → 规则与选项 → 扫描与预览 → 执行 → 结果）；列表行为状态徽章 + 原路径 → 目标路径，✎ 打开输入框按 Iwara ID 或链接重新查询；日志可折叠；结果页给出数字摘要 |
| 修复与工具的拖放文件夹 / 文件 | Flet 不能接收系统拖放：只保留“浏览”与输入路径 |
| 工具页单个长表单（设置在前、开始按钮在底部） | 判断卡片后紧接“输出 + 开始 + 进度”卡片，压缩设置放在最后（设置会保存，通常只改一次） |
| 封面预览的大图栏与 4 × 4 网格并排 | 改为网格在上、选中项大图在下；窗口太窄时网格为 2 列（方向键按行移动） |
| 手机底栏只有主要目的地，打开设置 / 规则等时底栏无选中项（Flutter 要求必须选中一项，会报错） | 当前目的地不在底栏时临时追加到底栏末尾并选中 |
| 下载页已完成任务的单击行为（旧版设置项生效，Flet 版此前未接入） | 桌面按设置打开文件夹或播放器（失败时提示原因）；手机打开资料库记录，设置项隐藏 |
| 浏览器“分享到 IwaraTool” | Flet 不能接收 intent：回到前台时若剪贴板里有新复制的 Iwara 链接，提示条“打开”进入详情或作者页 |
| 手机上的文件夹相关动作、自定义播放器命令、本机 aria2、修复、工具 | 直接隐藏（8.3），远端 aria2 在手机上固定开启远端模式 |

---

## 进度

| 日期 | 内容 |
| --- | --- |
| 2026-11-26 | 打包版桌面自检：`app/ui_flet/desktop_check.py` 在 `build_flet.py` 的冒烟测试里于打包应用内运行（临时数据目录）——开启“关闭后继续运行”发出关窗请求，须由客户端报告窗口隐藏 / 最小化或唤回时的 SHOW / RESTORE；Windows 托盘图标须在运行；按打包可执行文件写入、读回、删除开机自启条目；发出系统通知。本机 macOS 打包版通过（最小化到程序坞并恢复，自启命令 `open -a …/IwaraTool.app`）；CI Windows 打包版通过（隐藏到托盘并恢复，托盘图标运行）；CI 的 macOS 客户端不报告任何窗口事件，窗口部分记为“无法判定”（自启与通知通过）。顺带修正：`TrayService.show_window` 等待客户端置前最多 3 秒（CI Mac 上永不返回，会卡住托盘“退出”）。Android 模拟器增加 21:9 手机与折叠屏合上（外屏）。 |
| 2026-11-26 | P9 补齐与复测：桌面集成——系统通知（`core/system_notify.py`，完成 / 失败批量合并，窗口在前台时用应用内提示）、开机自启（`core/login_items.py`，与旧版条目分开）、Windows 托盘与关闭到托盘（`platform/tray_windows.py`，ctypes；macOS 关窗最小化到程序坞）。性能——资料库行与 `VideoGrid` 卡片改为 `ft.memo`，“加载更多”只构建新项（列表每 200 行由 0.15→0.9 s 降到中位 0.14 s；封面视图由 2.6→6.4 s 降到中位 0.19 s）；Flet 1.1.3 在被跳过的 memo 父组件下丢失上下文，`use_shell()` 回退到窗口注册的 Shell。补测滚动与 3 个并发下载（总时长相同；下载时 Flet 界面最坏延迟 8 ms，旧版 48 ms；完成事件均送达），对比脚本统计每次运行的 traceback（均为 0）。Android 模拟器冒烟进 CI（发现并修正 cloudscraper 启动崩溃）。结论改为：Flet 可替换旧界面，替换前手动试用打包版与真机验收。 |
| 2026-11-26 | P9 对比与决策：`scripts/tools/compare_frontends.py`（两边同一离线演示数据、1280×800、各 3 次取中位数）。启动到首帧 PySide6 0.87 s / Flet 0.48 s；首页加载后内存 377 / 318 MB（Flet 含 Flutter 客户端进程）；1 万条资料库打开 0.06 / 0.26 s，内存 491 / 339 MB。包体：Windows 32 / 54 MB，macOS 61 / 68 MB，Android 通用 APK 184 MB（发布时应按 ABI 拆分）。结论：**v1.3.0 不替换旧界面**，Flet 桌面版作为预览、Android 版首次提供；托盘与开机自启、系统通知补齐且 Android 真机验收通过后再替换桌面版。滚动流畅度与 3 个并发下载未脚本化，替换前手动核对（以上结论已被同日的“P9 补齐与复测”取代）。见 `docs/verification/flet/P9/`。 |
| 2026-11-26 | P8 Android（代码部分）：`ui_flet/platform/capabilities.py`（`Shell.caps`）集中判断平台能力，导航、路由、视频与任务菜单、设置卡片按能力隐藏；`platform/storage.py` 首次启动把默认下载目录移到应用专属外部目录；`bootstrap.checkpoint()` 与暂停时保存；下载进行时常亮；系统返回键经 `Shell.back()`（手机先回首页再退出）；回到前台检查剪贴板里的 Iwara 链接（代替分享 intent）；远端 aria2 在手机上固定；`usesCleartextTraffic`；`build_flet.py --platform apk` 与 CI `build-flet-android`。接上旧版“已完成任务单击行为”。修正修复批量计时在开机不久的机器上不立即回显首项（CI 失败原因）。`--platform android` 开发参数。截图见 `docs/verification/flet/P8/`；真机验收待做。 |
| 2026-11-26 | P7 修复与工具：修复中心（`stores/repair.py` + `pages/repair/`，五步流程、扫描批量回显、扫描后改选项重新规划、按 ID / 链接重新查询、停止、结果摘要）；工具页（`stores/tools.py` + `pages/tools/`，上传压缩：探测、判断卡片、选项保存、编码器随 ffmpeg、开始 / 取消 / 进度与剩余时间、失败日志、打开文件夹与转到封面预览；封面预览：16 张并行截图、3:2 裁切、方向键、保存此帧、复制时间码；ffmpeg 缺失空状态）。新迁入：`core/repair/runs.py`（旧 `RepairWorker` 的循环与批量）、`presentation/repair/`、`presentation/tools/`（旧修复页与工具页改为调用）。顺带修复手机底栏在设置 / 规则等溢出目的地时 `selected_index` 越界。`--demo` 的修复路由写入混乱文件名，工具路由载入 `IWARATOOL_DEMO_VIDEO`。截图见 `docs/verification/flet/P7/`。 |
| 2026-11-26 | P6 资料库、规则、设置：资料库（`stores/library.py` + `pages/library/`，表格 / 列表 / 网格、范围与作者筛选、按宽度隐藏列、记录面板、可撤销删除、清理已移走、重命名、导出 CSV）；规则页（列表-详情、各页面规则卡片、模板预览 / 预设 / 插入字段 / 检验、标签候选、未保存询问）；设置页（`pages/settings/`，9 个分类全部卡片即时保存、账号登录、代理应用、aria2 测试、ffmpeg 检测、备份恢复、快捷键录制、自定义首页 `/settings/home/rows`）；外壳新增新版本提示与“仍有下载时关闭询问”。新迁入共用层：`presentation/history/library.py`、`record_state.py`、`presentation/rule_editing.py`、`presentation/settings/{texts,catalog,values}.py`、`presentation/home/layout.py`、`presentation/updates.py`、`presentation.shortcuts` 的编辑与显示函数（旧资料库、规则页、设置页、首页栏目对话框、更新提示改为调用）。截图见 `docs/verification/flet/P6/`。 |
| 2026-11-26 | P5 订阅与作者：订阅页（`stores/subscriptions.py` + `pages/subscriptions/`，手机总览卡片与最近作品货架、`/subscriptions/<id>` 作品网格、宽屏双栏、源搜索与排序、添加（链接 / 用户名）、导入关注作者、全部刷新与单源刷新的进度条、停用 / 启用、删除确认、作品筛选 / 排序 / 标题搜索、状态徽章、选择、下载当前 / 选中 / 新增、标为已看、标为已移走与还原）；作者页 `/author/<用户名>`（资料卡、视频 / 图片标签、分页网格、批量下载）；`AuthorStatus`（程序内订阅与 Iwara 关注）；`components/feed_grid.py`（首页完整网格与作者页共用）、`services/dialogs.py`（确认与输入对话框）；`VideoGrid` 可指定所在栏宽。新迁入共用层：`presentation/subscription/text.py`、`source_actions.py`、`presentation/author/status.py`、`page.py`（旧订阅页、作者页、`AuthorStatusBar` 改为调用）。演示夹具的 `seed` 移入 `demo_fixture`，`--demo` 的订阅 / 资料库 / 作者路由会写入虚构记录。注意：`TextField` 不能放进 `wrap=True` 的 Row（宽度无界，整块渲染失败）。截图见 `docs/verification/flet/P5/`。 |
| 2026-11-26 | P4 首页、详情、播放：首页（`stores/home.py` + `pages/home/`，手机货架 / 宽屏 2 行网格、栏目标签、缓存优先与过期重查、有新内容时不打断勾选、折叠记忆、未登录提示、“更多 ›”进搜索或完整网格页 `/home/<栏目>/<标签>`）；详情页 `/detail/video|image/<id>`（`stores/detail.py` 保留最近 12 个作品供返回、相关作品侧栏 / 网格、评论分页与回复、点赞、标签跳搜索、复制链接、浏览器打开、作者主页）；内嵌播放器（flet-video 1.1.3，本地副本直接播放，远程经流代理并续签）；`VideoActions` 右键菜单与手机动作面板；搜索标签候选。新迁入共用层：`presentation/home/section_feed.py`（旧 `SectionBlock` 改为调用）、`presentation/media/detail_info.py`、`video_actions.py`、`presentation/search/tag_completion.py`、`detail_text.link_segments`、`formatting.format_age`。修正演示夹具把 API 的 `/image/<id>` 当成图片文件的问题。截图见 `docs/verification/flet/P4/`。 |
| 2026-11-26 | P3 搜索与下载闭环：搜索页（来源、范围、排序、筛选面板、搜索记录、网格、选择与加入下载、手机滚动加载 / 宽屏页码、Oreno3D 预解析与下载状态徽章）；下载页（链接栏、待确认卡片、状态标签、任务行与进度、取消/重试/恢复、优先级、打开文件夹、在资料库中查看、日志页）；`VideoCard` / `VideoGrid` / `DownloadSheet` / `FilterSheet`；`stores/tasks.py`、`stores/search.py`、`services/downloads.py`、`services/images.py`。新迁入共用层：`presentation/media/cards.py`、`presentation/search/options.py`、`post_filter_text.py`、搜索记录、`core/search/resolution.apply_oreno_link`、待确认文字。`main_flet.py --demo`（离线虚构数据）；截图见 `docs/verification/flet/P3/`。事件桥改为在页面上下文里分发（否则后台线程的事件无法触发重绘）。 |
| 2026-11-25 | P2 外壳完成：`layout.py`（档位、导航形态、网格计算）、`routes.py`、`navigation.py`（每个目的地一个返回栈）、`shell.py` + `stores/app_state.py`（声明式，`page.render`）、自适应导航与“账号与偏好”面板、全局快捷键（共用 `presentation.shortcuts`）、事件桥接入（语言、主题、分级、站点、登录、下载徽章）、`core/account.py`；`main_flet.py --viewport/--route/--theme/--lang/--account/--shot` 与 `scripts/tools/flet_viewports.py`；截图见 `docs/verification/flet/P2/`。 |
| 2026-11-25 | P1 核心整理（C1～C7）完成：取消令牌、搜索服务、首页/Oreno3D/订阅批量逻辑入 core，展示逻辑入 `presentation`，`app/bootstrap.py`，事件桥，无 Qt 守护测试。旧界面测试全部通过（本机焦点类用例受桌面焦点影响，以 CI 为准）。 |
| 2026-11-25 | P0 工程：pixi 拆为 `qt` / `flet` 两个 feature（旧界面 3.11，见 2.3 的核查结果）；测试分层；`scripts/tools/build_flet.py` 与 `packaging/flet/`；`main_flet.py` 冒烟报告（是否加载 Qt、数据目录）；打包后的数据目录取 `FLET_APP_STORAGE_DATA`。 |
| 2026-11-25 | 分支 `feat/flet-ui`。pixi 新增独立 `flet` 环境（Python 3.12、Flet 1.1.3，不含 PySide6）；设计令牌、主题、图标注册表、`StatusBadge`；主题样张 `scripts/tools/flet_theme_gallery.py`；`main_flet.py` 空壳；CI 新增 Flet 测试任务（Windows / macOS / Linux）。旧界面截图基线在 `tmp/pyside6_baseline/`（不入库）。 |

开发运行注意：`flet_desktop` 会优先启动 `<当前目录>/build/macos/*.app` 或 `build/windows/*.exe` 作为“上次 flet build 的客户端”，而本仓库这两个目录存放的是 Nuitka 构建的旧版应用。Flet 入口一律通过 `app/ui_flet/runner.py` 的 `run()` 启动，它在检测到这些目录时切换到中立的工作目录。

## 11. 待核实的 Flet 能力

实施对应功能前，先在 `scripts/tools/flet_probe/` 写最小样例验证，并把结论补进本节：

1. ~~Android 系统返回键与预测性返回是否触发 `on_view_pop` 或其他事件~~：只有一个视图时不触发 `on_view_pop`；根视图设 `can_pop=False` 并提供 `on_confirm_pop`，由 `View.confirm_pop(bool)` 决定（Flet 1.1.3 `view.dart` 的 `PopScope`，`enableOnBackInvokedCallback` 默认开启）【已核实（源码）；三种模拟器上首页按返回键回到桌面（CI `android-emulator`）；真机预测性返回手势待验】。
2. 折叠屏铰链与姿态信息（`displayFeatures`）。
3. `ResponsiveRow` 断点依据窗口宽度还是自身宽度。
4. `page.width` / `page.height` 在移动端启动时的取值时机。
5. 下拉刷新控件、可拖动排序列表。
6. ~~`VideoMedia` 是否支持 HTTP 请求头~~：支持（`http_headers`）；为了续签过期地址仍走 7.3 的流代理，本机模拟源站已验证 403 → 重签 → 继续播放。
7. ~~Android 应用私有目录与外部存储路径的获取方式~~：`FLET_APP_STORAGE_DATA` 与 `ft.StoragePaths()`（`get_external_storage_directory` 仅 Android）；分区存储下用目录选择器另选公共目录是否可写【真机待验】。
8. ~~Android 接收分享 intent~~：Flet 1.1.3 不支持（只有对外 `Share`），改为回到前台时检查剪贴板；~~常亮~~：`ft.Wakelock`（核心自带）；后台执行仍只保证前台。
9. ~~应用生命周期事件~~：`page.on_app_lifecycle_state_change`，状态含 `show` / `resume` / `hide` / `inactive` / `pause` / `detach`。
10. 桌面窗口最小尺寸属性、~~文件拖放~~（Flet 1.1.3 不支持系统文件拖入，修复与工具页只保留浏览）、托盘、系统通知。
11. 键盘事件接口名称与修饰键表示；macOS ⌘ 键。
12. ~~locale 配置（日文字形优先）~~（已用字体回退实现，见 5.3）、减少动态效果、系统字体缩放、~~视觉密度~~（`Theme.visual_density` 可用，桌面设为 COMPACT）。
13. Flet 组件测试工具。
14. ~~整页截图~~：`page.enable_screenshots = True` 后 `await page.take_screenshot()` 返回 PNG（已用于主题样张）。

## 参考资料

- [flet build 命令](https://flet.dev/docs/cli/flet-build/)
- [发布 Flet 应用](https://docs.flet.dev/publish/)
- [Flet 0.86 发布说明（多 Python 版本、默认 3.14）](https://flet.dev/blog/flet-v-0-86-release-announcement)
- [Flet 1.0 发布说明](https://flet.dev/blog/flet-1-0)、[声明式 UI 介绍](https://flet.dev/blog/introducing-declarative-ui-in-flet)
- [Page](https://flet.dev/docs/controls/page/)、[PageResizeEvent](https://flet.dev/docs/types/pageresizeevent/)、[PageMediaData](https://flet.dev/docs/types/pagemediadata/)
- [ResponsiveRow](https://flet.dev/docs/controls/responsiverow/)、[GridView](https://flet.dev/docs/controls/gridview/)、[SafeArea](https://flet.dev/docs/controls/safearea/)
- [NavigationRail](https://flet.dev/docs/controls/navigationrail/)、[NavigationBar](https://flet.dev/docs/controls/navigationbar/)、[NavigationDrawer](https://flet.dev/docs/controls/navigationdrawer/)
- [Video（flet-video）](https://flet.dev/docs/controls/video/)
- [flet-build-action](https://github.com/flet-dev/flet-build-action)、[flet#6839 默认排除规则不完整](https://github.com/flet-dev/flet/issues/6839)
- Material 3 窗口尺寸档位：<https://m3.material.io/foundations/layout/applying-layout/window-size-classes>
