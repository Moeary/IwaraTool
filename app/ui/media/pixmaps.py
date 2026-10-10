"""Decode cover images at display size and keep them in a byte-bounded LRU."""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QImageReader, QPixmap


def read_pixmap(path: str, max_width: int = 0) -> QPixmap:
    """A picture from disk, decoded no larger than ``max_width`` pixels wide.

    The reader scales while decoding (JPEG decodes straight to a fraction of its
    size), so a 3200 x 2000 original never exists in memory at full size: that
    is 25 MB as a pixmap, and image posts have dozens of them.
    """

    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if max_width > 0 and size.isValid() and size.width() > max_width:
        reader.setScaledSize(QSize(max_width, max(1, round(size.height() * max_width / size.width()))))
    image = reader.read()
    return QPixmap.fromImage(image) if not image.isNull() else QPixmap()


def image_size(path: str) -> QSize:
    """Pixel size of an image file from its header (nothing is decoded)."""

    return QImageReader(path).size()


class PixmapLRU:
    """Decoded covers, kept up to a byte budget and re-read from disk when evicted.

    A dict of every cover ever shown grows with the library (hundreds of
    ~300 KB pixmaps); the files are cached on disk anyway.
    """

    def __init__(self, max_bytes: int = 48 * 1024 * 1024):
        self._max = max_bytes
        self._items: dict[str, QPixmap] = {}
        self._bytes = 0

    @staticmethod
    def _size(pixmap: QPixmap) -> int:
        return pixmap.width() * pixmap.height() * max(1, pixmap.depth() // 8)

    def get(self, key: str, default=None):
        pixmap = self._items.pop(key, None)
        if pixmap is None:
            return default
        self._items[key] = pixmap  # most recently used goes last
        return pixmap

    def __setitem__(self, key: str, pixmap: QPixmap):
        old = self._items.pop(key, None)
        if old is not None:
            self._bytes -= self._size(old)
        self._items[key] = pixmap
        self._bytes += self._size(pixmap)
        while self._bytes > self._max and len(self._items) > 1:
            _key, evicted = next(iter(self._items.items()))
            del self._items[_key]
            self._bytes -= self._size(evicted)

    def __contains__(self, key: str) -> bool:
        return key in self._items

    def __getitem__(self, key: str) -> QPixmap:
        pixmap = self.get(key)
        if pixmap is None:
            raise KeyError(key)
        return pixmap

    def __len__(self) -> int:
        return len(self._items)

    def clear(self):
        self._items.clear()
        self._bytes = 0
