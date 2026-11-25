"""IwaraTool — Flet interface entry point (v1.3.0 trial; see docs/flet_ui_plan.md).

    pixi run -e flet start-flet

Development options (docs/flet_ui_plan.md 9.2):

    --viewport WxH     start with this window size (simulates phones and tablets)
    --route PATH       open this route first (``/settings/account``)
    --theme MODE       auto / light / dark for this run only
    --lang LANG        zh / en / ja (saved: point IWARATOOL_DATA_DIR elsewhere for checks)
    --shot FILE.png    save a screenshot after start-up and quit
    --account          open the account panel (for screenshots)
    --demo             offline made-up Iwara in a throw-away data folder (scripts/tools/demo_fixture.py)
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from app.ui_flet.runner import loaded_qt_modules, prepare_environment, run

# Before the rest of ``app`` is imported (the core reads these at import time).
prepare_environment(Path(__file__).resolve().parent)

if "--demo" in sys.argv:
    # Made-up offline Iwara and a throw-away data folder (scripts/tools/demo_fixture.py).
    sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts" / "tools"))
    import demo_fixture  # noqa: E402

    _lang = sys.argv[sys.argv.index("--lang") + 1] if "--lang" in sys.argv else "zh"
    demo_fixture.install(language={"zh": "zh_CN", "ja": "ja_JP"}.get(_lang, "en_US"))

from app import bootstrap  # noqa: E402

# A restore staged in Settings must land before app.config opens any file.
bootstrap.restore_pending_backup()

import flet as ft  # noqa: E402

from app.paths import app_data_dir  # noqa: E402
from app.ui_flet import routes  # noqa: E402
from app.ui_flet.app import mount  # noqa: E402
from app.ui_flet.lifecycle import shutdown, start_app  # noqa: E402
from app.version import __version__  # noqa: E402

# Smoke test: ``--smoke-test`` or IWARATOOL_SMOKE_TEST=1 (packaged apps may not
# pass arguments). With IWARATOOL_SMOKE_REPORT set, a JSON report is written there.
SMOKE_TEST = "--smoke-test" in sys.argv or os.environ.get("IWARATOOL_SMOKE_TEST") == "1"


def write_smoke_report(page: ft.Page) -> None:
    target = os.environ.get("IWARATOOL_SMOKE_REPORT")
    if not target:
        return
    report = {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": str(page.platform.value if page.platform else ""),
        "packaged": bool(os.environ.get("FLET_PLATFORM")),
        "data_dir": str(app_data_dir()),
        "flet_storage": os.environ.get("FLET_APP_STORAGE_DATA", ""),
        "qt_modules": loaded_qt_modules(),
        "bootstrap": "prepared",
    }
    Path(target).write_text(json.dumps(report, indent=2), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--viewport", default="")
    parser.add_argument("--route", default="")
    parser.add_argument("--theme", choices=("auto", "light", "dark"), default="")
    parser.add_argument("--lang", choices=("zh", "en", "ja"), default="")
    parser.add_argument("--shot", type=Path)
    parser.add_argument("--account", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--wait", type=float, default=1.5, help="seconds before --shot")
    args, _unknown = parser.parse_known_args(argv)  # packaged runs may add their own
    if args.shot:
        args.shot = args.shot.resolve()  # the runner changes the working directory
    return args


ARGS = parse_args(sys.argv[1:])


def viewport(text: str) -> tuple[int, int] | None:
    try:
        width, height = (int(v) for v in text.lower().split("x"))
    except ValueError:
        return None
    return width, height


async def take_shot(page: ft.Page, target: Path) -> None:
    await asyncio.sleep(ARGS.wait)  # let fonts, layout, the first resize (and demo work) settle
    data = await page.take_screenshot(pixel_ratio=1)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(f"saved {target}", flush=True)
    await shutdown(page)


async def main(page: ft.Page) -> None:
    page.title = "IwaraTool"
    page.padding = 0
    page.window.min_width, page.window.min_height = 320, 560
    size = viewport(ARGS.viewport) or (1280, 800)
    page.window.min_width = min(page.window.min_width, size[0])
    page.window.min_height = min(page.window.min_height, size[1])
    page.window.width, page.window.height = size
    page.enable_screenshots = bool(ARGS.shot)
    if ARGS.lang:
        from app.presentation.preferences import set_language

        set_language(ARGS.lang)
    shell = mount(page, theme_override=ARGS.theme)
    if ARGS.route:
        route = routes.parse(ARGS.route)
        if route.is_root:
            shell.select(route.destination)  # a start page, not a step Back returns from
        else:
            shell.open(ARGS.route)
    if ARGS.account:
        shell.open_account()
    # The smoke test only prepares (no task resume, no timed network checks).
    services = not (SMOKE_TEST or ARGS.shot) or ARGS.demo
    await start_app(page, services=services)
    if SMOKE_TEST:
        write_smoke_report(page)
        await shutdown(page)
        return
    await shell.refresh_badges()
    await shell.tasks.reload()
    if services:
        page.run_task(shell.restore_login)
    if ARGS.shot:
        await take_shot(page, ARGS.shot)


if __name__ == "__main__":
    run(main)
