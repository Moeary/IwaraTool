"""IwaraTool — entry point."""
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication


def smoke_test():
    """Check packaged Qt and assets before opening databases or network jobs."""
    import time
    from PySide6.QtCore import QCoreApplication, QEvent, QUrl
    from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
    from PySide6.QtMultimediaWidgets import QVideoWidget
    from app.qt_runtime import ensure_multimedia_plugins
    from app.version import __version__

    app = QApplication(sys.argv)
    ensure_multimedia_plugins()
    player = QMediaPlayer(app)
    widget = QVideoWidget()
    player.setVideoOutput(widget)
    if not player.isAvailable():
        raise RuntimeError("Qt multimedia backend is unavailable")
    root = Path(__file__).resolve().parent
    for relative in (
        "app/icon.ico",
        "app/data/tag_translations/loveiwara_iwara_tags_localized.json",
        "app/data/oreno3d_iwara_map.json",
    ):
        if not (root / relative).is_file():
            raise RuntimeError(f"Missing bundled resource: {relative}")
    app.processEvents()
    if "--smoke-media" in sys.argv:
        media_index = sys.argv.index("--smoke-media") + 1
        media = Path(sys.argv[media_index]).resolve()
        decoded = []
        sink = QVideoSink(app)
        sink.videoFrameChanged.connect(lambda frame: decoded.append(True) if frame.isValid() else None)
        player.setVideoOutput(sink)
        player.setSource(QUrl.fromLocalFile(str(media)))
        player.play()
        deadline = time.monotonic() + 15
        while not decoded and time.monotonic() < deadline:
            app.processEvents()
            if player.error() != QMediaPlayer.Error.NoError:
                raise RuntimeError(f"Smoke video playback failed: {player.errorString()}")
            time.sleep(0.01)
        if not decoded:
            raise RuntimeError("Smoke video did not produce a decoded frame")
        player.stop()
        print("H.264/MP4 video decode OK", flush=True)
    print(f"IwaraTool {__version__}: Qt multimedia and bundled resources OK", flush=True)
    player.setVideoOutput(None)
    player.deleteLater()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__" and "--smoke-test" in sys.argv:
    smoke_test()
    raise SystemExit(0)

from app.core.backup import apply_pending_restore
from app.paths import app_data_dir

# A restore staged from the settings page must land before any database or
# settings file is opened, which happens as soon as app.config is imported.
apply_pending_restore(str(app_data_dir()))

from app.config import app_config  # noqa: E402
from app.logging_setup import get_logger, setup_logging
from app.qt_runtime import install_console_interrupt
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
    install_console_interrupt(window.quit_from_console, app)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
