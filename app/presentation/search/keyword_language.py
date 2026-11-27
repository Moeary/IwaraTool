"""Tell Chinese keywords from Japanese ones."""

from __future__ import annotations

import re


_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


_KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]")


def is_chinese_keyword(text: str) -> bool:
    """Han characters without any kana: Chinese rather than Japanese."""

    return bool(_HAN.search(text or "")) and not _KANA.search(text or "")
