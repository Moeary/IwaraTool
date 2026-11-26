"""The Tools hub of the new interface: one list of every tool (rules, repair, the upload tools).

Keys are the hub's own; each front end maps them to its pages.  Order is the
maintainer's (2026-11-26): rules first, repair second, then the upload tools.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...i18n import tr
from . import common


@dataclass(frozen=True)
class HubEntry:
    key: str  # "rules", "repair", or a tool key from common.tools()
    title: str
    summary: str
    group: str


def hub_title() -> str:
    return tr("Tools", "工具", "ツール")


def group_titles() -> dict[str, str]:
    return {
        "organize": tr("Download and organize", "下载与整理", "保存と整理"),
        "upload": tr("Upload", "上传", "アップロード"),
    }


def entries(*, desktop: bool = True) -> tuple[HubEntry, ...]:
    items = [HubEntry("rules", tr("Download rules", "下载规则", "ダウンロードルール"),
                      tr("Naming, filters and what a download saves", "命名、筛选与下载内容", "命名・フィルター・保存内容"),
                      "organize")]
    if desktop:
        items.append(HubEntry("repair", tr("Repair", "修复", "修復"),
                              tr("Rename and sort downloaded files by a rule", "按规则重命名、整理已下载的文件",
                                 "保存済みファイルをルールで名前変更・整理"), "organize"))
        items += [HubEntry(t.key, t.title, t.summary, "upload") for t in common.tools()]
    return tuple(items)
