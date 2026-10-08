"""IwaraTool — entry point."""
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.core.backup import apply_pending_restore

# A restore staged from the settings page must land before any database or
# settings file is opened, which happens as soon as app.config is imported.
apply_pending_restore(str(Path(sys.argv[0]).resolve().parent / "data"))

from app.config import app_config  # noqa: E402
from app.logging_setup import get_logger, setup_logging
from app.signal_bus import signal_bus
from app.core.manager import download_manager
from app.core.background_services import background_service
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme_mode


def main():
    setup_logging()
    get_logger(__name__).info("IwaraTool starting")
    # Mirror the in-app log panel into data/logs so packaged builds stay debuggable.
    ui_logger = get_logger("ui")
    signal_bus.log_message.connect(lambda message: ui_logger.info("%s", message))

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

    # Apply the saved theme mode; "auto" follows the system dark/light mode.
    apply_theme_mode(app_config.theme_mode)

    # Apply proxy config on startup
    download_manager.apply_config()

    # Restore cached token first for faster startup auth.
    download_manager.restore_cached_login()

    window = MainWindow()
    download_manager.start()
    background_service.start()
    app.aboutToQuit.connect(lambda: background_service.stop(wait=False))
    app.aboutToQuit.connect(lambda: download_manager.shutdown(wait=False))
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
