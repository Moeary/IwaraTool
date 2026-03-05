"""IwaraTool — entry point."""
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from qfluentwidgets import setTheme, Theme

from app.config import app_config
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

    # Auto-login if credentials saved
    if app_config.auth_enabled and app_config.username and app_config.password:
        from app.core.manager import download_manager as dm
        from concurrent.futures import ThreadPoolExecutor
        _boot_pool = ThreadPoolExecutor(max_workers=1)
        _boot_pool.submit(dm.api.login, app_config.username, app_config.password)

    window = MainWindow()
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
