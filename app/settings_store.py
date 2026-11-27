"""The settings file behind ``app.config``: ``config.ini`` in QSettings' INI format.

Versions up to v1.2 wrote the file with Qt's ``QSettings``.
:class:`IniSettingsStore` reads and writes the same ``config.ini`` with the
same layout and the subset of the QSettings API the app uses (``value``,
``setValue``, ``contains``, ``remove``, ``allKeys``, ``clear``, ``sync``), so
older files keep working and ``app.core.backup`` can treat it as INI text.

Compatibility covers what the app stores: strings, ints, floats, bools and
``None``. Read back from disk they come out as strings, as they do from
QSettings, and an unquoted comma makes a list of strings. Qt-only values
(``@ByteArray(…)``, ``@Variant(…)``) are kept as :class:`RawIniValue` and
written back unchanged.
"""

from __future__ import annotations

import atexit
import os
import tempfile
import threading
import weakref
from typing import Any


GENERAL = "General"
_KEY_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
_ESCAPES = {"\a": "a", "\b": "b", "\f": "f", "\n": "n", "\r": "r", "\t": "t", "\v": "v", '"': '"', "\\": "\\"}
_UNESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", '"': '"', "'": "'", "?": "?", "\\": "\\", ";": ";", ",": ","}
_HEX = "0123456789abcdefABCDEF"


class RawIniValue(str):
    """A Qt-encoded value (``@ByteArray(…)``, ``@Variant(…)``) kept verbatim."""


# ── keys ────────────────────────────────────────────────────────────────────


def _escape_key_part(part: str) -> str:
    out = []
    for ch in part:
        if ch in _KEY_SAFE:
            out.append(ch)
        elif ch == "/":
            out.append("\\")
        elif ord(ch) < 256:
            out.append(f"%{ord(ch):02X}")
        else:
            out.append(f"%U{ord(ch):04X}")
    return "".join(out)


def _unescape_key_part(text: str) -> str:
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            out.append("/")
            i += 1
        elif ch == "%" and text[i + 1 : i + 2] == "U" and len(text) >= i + 6:
            out.append(chr(int(text[i + 2 : i + 6], 16)))
            i += 6
        elif ch == "%" and len(text) >= i + 3:
            out.append(chr(int(text[i + 1 : i + 3], 16)))
            i += 3
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _split_key(key: str) -> tuple[str, str]:
    """Section and key-in-section, as QSettings lays ``a/b/c`` out (``[a]`` ``b\\c``)."""

    key = key.strip("/")
    if "/" in key:
        section, rest = key.split("/", 1)
        return section, rest
    return GENERAL, key


# ── values ──────────────────────────────────────────────────────────────────


def _escape_string(text: str) -> str:
    if text.startswith("@"):
        text = "@" + text
    out = []
    needs_quotes = False
    hex_pending = False
    for ch in text:
        if ch in ";,=":
            needs_quotes = True
        if ch in _ESCAPES:
            out.append("\\" + _ESCAPES[ch])
            hex_pending = False
        elif ord(ch) < 0x20 or ch == "\x7f":
            out.append(f"\\x{ord(ch):x}")
            hex_pending = True
        elif hex_pending and ch in _HEX:
            out.append(f"\\x{ord(ch):x}")
        else:
            out.append(ch)
            hex_pending = False
    result = "".join(out)
    if needs_quotes or (result and (result[0].isspace() or result[-1].isspace())):
        result = f'"{result}"'
    return result


def encode_value(value: Any) -> str:
    """Text QSettings would write for ``value``."""

    if value is None:
        return "@Invalid()"
    if isinstance(value, RawIniValue):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    if isinstance(value, str):
        return _escape_string(value)
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        if not value:
            return "@Invalid()"
        return ", ".join(_escape_string(item) for item in value)
    raise TypeError(f"IniSettingsStore cannot store {type(value).__name__} values without Qt")


def decode_value(text: str) -> Any:
    """What QSettings returns for INI value ``text`` when read from disk."""

    raw = text.strip()
    if raw.startswith("@") and not raw.startswith("@@"):
        return None if raw == "@Invalid()" else RawIniValue(raw)
    items: list[str] = []
    current: list[str] = []
    quoted = False
    in_quotes = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == '"':
            in_quotes = not in_quotes
            quoted = True
            i += 1
        elif ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt in _UNESCAPES:
                current.append(_UNESCAPES[nxt])
                i += 2
            elif nxt in "xX":
                j = i + 2
                while j < len(raw) and raw[j] in _HEX:
                    j += 1
                current.append(chr(int(raw[i + 2 : j] or "0", 16)))
                i = j
            elif nxt in "01234567":
                j = i + 1
                while j < len(raw) and j < i + 4 and raw[j] in "01234567":
                    j += 1
                current.append(chr(int(raw[i + 1 : j], 8)))
                i = j
            else:
                current.append(nxt)
                i += 2
        elif ch == "," and not in_quotes:
            items.append(_finish_item(current, quoted))
            current, quoted = [], False
            i += 1
        elif ch == ";" and not in_quotes:
            break  # the rest is a comment
        else:
            current.append(ch)
            i += 1
    last = _finish_item(current, quoted)
    if items:
        return items + [last]
    if last.startswith("@@"):
        return last[1:]
    return last


def _finish_item(chars: list[str], quoted: bool) -> str:
    text = "".join(chars)
    return text if quoted else text.strip()


# ── store ───────────────────────────────────────────────────────────────────


class IniSettingsStore:
    """QSettings-compatible INI settings file in pure Python."""

    def __init__(self, path: str):
        self._path = os.fspath(path)
        self._lock = threading.RLock()
        self._values: dict[str, Any] = {}
        self._dirty = False
        self._load()
        _open_stores.add(self)

    def fileName(self) -> str:  # noqa: N802 - QSettings name
        return self._path

    def _load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except FileNotFoundError:
            return
        section = GENERAL
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith(";") or stripped.startswith("#"):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                section = _unescape_key_part(stripped[1:-1])
                continue
            if "=" not in stripped:
                continue
            key_text, value_text = stripped.split("=", 1)
            name = _unescape_key_part(key_text.strip())
            full = name if section == GENERAL else f"{section}/{name}"
            self._values[full] = decode_value(value_text)

    def value(self, key: str, defaultValue: Any = None) -> Any:  # noqa: N803 - QSettings name
        with self._lock:
            return self._values.get(key.strip("/"), defaultValue)

    def setValue(self, key: str, value: Any) -> None:  # noqa: N802 - QSettings name
        encode_value(value)  # refuse what cannot be written, as early as possible
        with self._lock:
            self._values[key.strip("/")] = value
            self._dirty = True

    def contains(self, key: str) -> bool:
        with self._lock:
            return key.strip("/") in self._values

    def remove(self, key: str) -> None:
        """Remove ``key`` and every key under it (QSettings removes the group too)."""

        key = key.strip("/")
        with self._lock:
            doomed = [k for k in self._values if not key or k == key or k.startswith(key + "/")]
            for k in doomed:
                del self._values[k]
            if doomed:
                self._dirty = True

    def allKeys(self) -> list[str]:  # noqa: N802 - QSettings name
        with self._lock:
            return sorted(self._values)

    def clear(self) -> None:
        with self._lock:
            if self._values:
                self._values.clear()
                self._dirty = True

    def sync(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            text = self._render()
            directory = os.path.dirname(self._path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                os.replace(tmp, self._path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            self._dirty = False

    def _render(self) -> str:
        sections: dict[str, list[tuple[str, str]]] = {}
        for key, value in self._values.items():
            section, name = _split_key(key)
            sections.setdefault(section, []).append((_escape_key_part(name), encode_value(value)))
        order = sorted(sections, key=lambda s: (s != GENERAL, _escape_key_part(s)))
        blocks = []
        for section in order:
            lines = [f"[{_escape_key_part(section)}]"]
            lines += [f"{name}={text}" for name, text in sorted(sections[section])]
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks) + ("\n" if blocks else "")


_open_stores: "weakref.WeakSet[IniSettingsStore]" = weakref.WeakSet()


def sync_all_open_stores() -> None:
    """Write what ``sync=False`` left pending in every open store, as QSettings does on destruction.

    ``app.bootstrap.stop()`` calls this on purpose; the ``atexit`` hook only
    covers exits that never reach it.
    """

    for store in list(_open_stores):
        try:
            store.sync()
        except Exception:
            pass


atexit.register(sync_all_open_stores)
_flush_open_stores = sync_all_open_stores  # the old private name


def open_settings(path: str) -> "IniSettingsStore":
    """The settings store for ``path``."""

    return IniSettingsStore(path)
