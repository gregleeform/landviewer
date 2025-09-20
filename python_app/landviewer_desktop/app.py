"""Application bootstrap helpers."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from landviewer_desktop.main_window import MainWindow


def run() -> None:
    """Create the Qt event loop and show the main window."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
