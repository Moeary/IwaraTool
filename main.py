"""IwaraTool — entry point."""
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from qfluentwidgets import setTheme, Theme

from app.core.manager import download_manager
from app.ui.main_window import MainWindow


def main():
    # Allow high-DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("IwaraTool")
    app.setOrganizationName("IwaraTool")

    icon_path = Path(__file__).resolve().parent / "app" / "icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Apply Fluent theme (auto follows system dark/light mode)
    setTheme(Theme.AUTO)

    # Apply proxy config on startup
    download_manager.apply_config()

    # Restore cached token first for faster startup auth.
    download_manager.restore_cached_login()

    window = MainWindow()
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
