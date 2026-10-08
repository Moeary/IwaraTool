"""Shared design tokens and theme helpers for every IwaraTool page.

All pages pull colors from one light/dark palette so switching themes keeps
the same hierarchy (accent, surfaces, status colors) instead of each page
inventing its own hex values.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSplitter, QSplitterHandle, QWidget

from qfluentwidgets import Theme, isDarkTheme, qconfig, setTheme
from qfluentwidgets.common.style_sheet import ThemeColor

from ..core.models import TaskStatus

# Page chrome shared by every sub-interface.
PAGE_MARGINS = (28, 20, 28, 18)
PAGE_SPACING = 12
CARD_MARGINS = (16, 14, 16, 14)


@dataclass(frozen=True)
class Palette:
    accent: str
    text: str
    text_secondary: str
    text_disabled: str
    surface: str
    border: str
    hover: str
    selected: str
    selected_text: str
    placeholder: str
    scroll_handle: str
    splitter_hover: str
    splitter_pressed: str
    grip: str
    success: str
    warning: str
    danger: str
    info: str
    purple: str
    neutral: str
    success_bg: str
    warning_bg: str


LIGHT = Palette(
    accent="#00848e",
    text="#1b1b1b",
    text_secondary="#5f6368",
    text_disabled="#a0a4a8",
    surface="#ffffff",
    border="#e1e4e8",
    hover="rgba(0, 0, 0, 0.045)",
    selected="#d9eef0",
    selected_text="#0b2f33",
    placeholder="#e8ebef",
    scroll_handle="rgba(0, 0, 0, 0.32)",
    splitter_hover="rgba(0, 0, 0, 0.06)",
    splitter_pressed="rgba(0, 0, 0, 0.12)",
    grip="rgba(0, 0, 0, 0.20)",
    success="#0f7b0f",
    warning="#9d5d00",
    danger="#c42b1c",
    info="#005fb8",
    purple="#7a4fb0",
    neutral="#616161",
    success_bg="#dff3e2",
    warning_bg="#fff1d6",
)

DARK = Palette(
    accent="#4cc2c9",
    text="#f3f3f3",
    text_secondary="#b4b8bc",
    text_disabled="#6c7075",
    surface="#2b2b2b",
    border="#3d3d3d",
    hover="rgba(255, 255, 255, 0.06)",
    selected="#2c4b4d",
    selected_text="#ffffff",
    placeholder="#333536",
    scroll_handle="rgba(255, 255, 255, 0.30)",
    splitter_hover="rgba(255, 255, 255, 0.06)",
    splitter_pressed="rgba(255, 255, 255, 0.12)",
    grip="rgba(255, 255, 255, 0.24)",
    success="#6ccb5f",
    warning="#f0b44c",
    danger="#ff99a4",
    info="#60cdff",
    purple="#c3a6ff",
    neutral="#a8abae",
    success_bg="#24412b",
    warning_bg="#46391f",
)


def palette() -> Palette:
    return DARK if isDarkTheme() else LIGHT


def to_qcolor(value: str) -> QColor:
    """Parse palette values, including CSS ``rgba(r, g, b, a)`` strings.

    QColor only understands hex/named colors; an rgba() string would silently
    become an invalid (black) color when painted.
    """
    text = str(value).strip()
    if text.startswith("rgba(") and text.endswith(")"):
        r, g, b, a = (part.strip() for part in text[5:-1].split(","))
        return QColor(int(r), int(g), int(b), round(float(a) * 255))
    return QColor(text)


def qcolor(name: str) -> QColor:
    """Return a palette color for the current theme as a QColor."""
    return to_qcolor(getattr(palette(), name))


# ── Accent color ────────────────────────────────────────────────────────────

_ACCENT_INSTALLED = False


def _themed_accent(self: ThemeColor) -> QColor:
    """Accent ramp with a softer dark-mode base than qfluentwidgets' default.

    The stock implementation forces value=1.0 in dark mode, which turns teal
    into neon cyan. Deriving the ramp from a tuned per-theme base keeps light
    and dark buttons recognisably the same brand color.
    """
    dark = isDarkTheme()
    h, s, v, _ = QColor(DARK.accent if dark else LIGHT.accent).getHsvF()
    if dark:
        factors = {
            ThemeColor.DARK_1: (1.0, 0.9),
            ThemeColor.DARK_2: (0.977, 0.82),
            ThemeColor.DARK_3: (0.95, 0.7),
            ThemeColor.LIGHT_1: (0.92, 1.06),
            ThemeColor.LIGHT_2: (0.78, 1.1),
            ThemeColor.LIGHT_3: (0.65, 1.14),
        }
    else:
        factors = {
            ThemeColor.DARK_1: (1.0, 0.75),
            ThemeColor.DARK_2: (1.05, 0.5),
            ThemeColor.DARK_3: (1.1, 0.4),
            ThemeColor.LIGHT_1: (1.0, 1.05),
            ThemeColor.LIGHT_2: (0.75, 1.05),
            ThemeColor.LIGHT_3: (0.65, 1.05),
        }
    s_factor, v_factor = factors.get(self, (1.0, 1.0))
    return QColor.fromHsvF(h, min(s * s_factor, 1.0), min(v * v_factor, 1.0))


def install_accent():
    """Route every qfluentwidgets accent lookup through the shared palette."""
    global _ACCENT_INSTALLED
    if _ACCENT_INSTALLED:
        return
    ThemeColor.color = _themed_accent
    _ACCENT_INSTALLED = True


# ── Theme mode persistence ──────────────────────────────────────────────────

THEME_MODES = ("auto", "light", "dark")
_THEME_BY_MODE = {"auto": Theme.AUTO, "light": Theme.LIGHT, "dark": Theme.DARK}


def normalize_theme_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in THEME_MODES else "auto"


def apply_theme_mode(mode: str):
    """Apply a persisted theme mode ('auto', 'light' or 'dark')."""
    install_accent()
    theme = _THEME_BY_MODE[normalize_theme_mode(mode)]
    if qconfig.theme != theme or theme == Theme.AUTO:
        setTheme(theme)


# ── Labels ──────────────────────────────────────────────────────────────────

def set_secondary_text(label):
    """Give a Fluent label the shared secondary text color in both themes."""
    label.setTextColor(QColor(LIGHT.text_secondary), QColor(DARK.text_secondary))
    return label


def set_status_text(label, role: str):
    """Color a Fluent label with a semantic palette role ('success', ...)."""
    label.setTextColor(QColor(getattr(LIGHT, role)), QColor(getattr(DARK, role)))
    return label


def summary_text(parts: list[tuple[str, object]]) -> str:
    """Join "label: value" pairs with a quiet middle-dot separator."""
    return "  ·  ".join(f"{label} {value}" for label, value in parts)


# ── Status colors ───────────────────────────────────────────────────────────

_TASK_STATUS_ROLES: dict[TaskStatus, str] = {
    TaskStatus.QUEUED_META: "neutral",
    TaskStatus.RESOLVING: "info",
    TaskStatus.QUEUED_DOWNLOAD: "purple",
    TaskStatus.DOWNLOADING: "success",
    TaskStatus.CANCELLING: "warning",
    TaskStatus.CANCELLED: "neutral",
    TaskStatus.SKIPPED: "warning",
    TaskStatus.COMPLETED: "success",
    TaskStatus.FAILED: "danger",
}


def task_status_color(status: TaskStatus | None) -> QColor:
    return qcolor(_TASK_STATUS_ROLES.get(status, "neutral"))


def link_color(enabled: bool = True) -> QColor:
    """Color for clickable action cells inside tables."""
    return qcolor("accent" if enabled else "text_disabled")


# ── Style sheets ────────────────────────────────────────────────────────────

def scrollbar_qss() -> str:
    """Compact native scrollbar matching qfluentwidgets' overlay bars."""
    p = palette()
    return f"""
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px 2px 3px 2px; }}
    QScrollBar::handle:vertical {{ background: {p.scroll_handle}; min-height: 36px; border-radius: 3px; margin: 0 1px; }}
    QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background: {p.accent}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; height: 0px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 3px 2px 3px; }}
    QScrollBar::handle:horizontal {{ background: {p.scroll_handle}; min-width: 36px; border-radius: 3px; margin: 1px 0; }}
    QScrollBar::handle:horizontal:hover, QScrollBar::handle:horizontal:pressed {{ background: {p.accent}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; width: 0px; }}
    """


def apply_scrollbars(widget: QWidget):
    """Style only a view's native scrollbars.

    Setting QSS on a TableWidget/ListWidget itself would replace the
    qfluentwidgets theme stylesheet, so the bars are styled individually.
    """
    qss = scrollbar_qss()
    for getter in ("verticalScrollBar", "horizontalScrollBar"):
        bar = getattr(widget, getter, lambda: None)()
        if bar is not None:
            bar.setStyleSheet(qss)


def popup_list_qss() -> str:
    """Floating suggestion/history list surfaces."""
    p = palette()
    return f"""
    QListWidget {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 4px;
        color: {p.text};
        outline: none;
    }}
    QListWidget::item {{ padding: 7px 9px; border-radius: 5px; }}
    QListWidget::item:hover {{ background: {p.hover}; }}
    QListWidget::item:selected {{ background: {p.selected}; color: {p.selected_text}; }}
    """


def cover_grid_qss(object_name: str) -> str:
    """Thumbnail grids used by the search and subscription pages."""
    p = palette()
    return f"""
    QListWidget#{object_name} {{
        background-color: transparent;
        border: none;
        outline: none;
        color: {p.text};
    }}
    QListWidget#{object_name}::item {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 8px;
        color: {p.text};
        padding: 0px;
    }}
    QListWidget#{object_name}::item:hover {{
        background-color: {p.hover};
        border-color: {p.border};
    }}
    QListWidget#{object_name}::item:selected {{
        background-color: {p.selected};
        border: 1px solid {p.accent};
        color: {p.selected_text};
    }}
    """


# ── Splitter ────────────────────────────────────────────────────────────────

class _FluentSplitterHandle(QSplitterHandle):
    """Transparent handle with a small rounded grip that lights up on hover.

    It paints itself instead of using a style sheet: a style sheet on the
    splitter would cascade into every child widget of both panes, switching
    them to QStyleSheetStyle and delaying their layout updates.
    """

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter):
        super().__init__(orientation, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._hovered = False
        self._pressed = False

    def refresh_style(self):
        self.update()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._pressed = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event):
        p = palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if self._pressed or self._hovered:
            painter.setBrush(to_qcolor(p.splitter_pressed if self._pressed else p.splitter_hover))
            painter.drawRoundedRect(QRectF(self.rect()), 4, 4)
        if self.orientation() == Qt.Orientation.Vertical:
            grip_width, grip_height = min(48, max(24, self.width() - 12)), 4
        else:
            grip_width, grip_height = 4, min(48, max(24, self.height() - 12))
        grip = QRectF(
            max(0, (self.width() - grip_width) / 2),
            max(0, (self.height() - grip_height) / 2),
            grip_width,
            grip_height,
        )
        painter.setBrush(to_qcolor(p.accent if (self._hovered or self._pressed) else p.grip))
        painter.drawRoundedRect(grip, 2, 2)


class FluentSplitter(QSplitter):
    """QSplitter whose handles share the app-wide grip look."""

    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None):
        super().__init__(orientation, parent)
        self.setHandleWidth(10)

    def createHandle(self):
        return _FluentSplitterHandle(self.orientation(), self)

    def refresh_theme_style(self):
        for index in range(self.count()):
            handle = self.handle(index)
            if isinstance(handle, _FluentSplitterHandle):
                handle.refresh_style()


def style_splitter(splitter: QSplitter):
    """Theme a plain QSplitter; FluentSplitter handles paint themselves."""
    if isinstance(splitter, FluentSplitter):
        splitter.refresh_theme_style()
        return
    p = palette()
    splitter.setStyleSheet(
        f"""
        QSplitter::handle {{ background: transparent; border-radius: 4px; }}
        QSplitter::handle:hover {{ background: {p.splitter_hover}; }}
        QSplitter::handle:pressed {{ background: {p.splitter_pressed}; }}
        """
    )


def refresh_splitters(root: QWidget):
    """Re-color every FluentSplitter below ``root`` after a theme switch."""
    splitters = root.findChildren(FluentSplitter)
    if isinstance(root, FluentSplitter):
        splitters.append(root)
    for splitter in splitters:
        splitter.refresh_theme_style()
