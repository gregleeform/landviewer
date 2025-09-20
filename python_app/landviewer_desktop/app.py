"""Application bootstrap helpers."""
from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from landviewer_desktop.main_window import MainWindow


def run() -> None:
    """Create the Qt event loop and show the main window."""
    app = QApplication(sys.argv)
    _apply_dark_theme(app)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def _apply_dark_theme(app: QApplication) -> None:
    """Apply a dark palette and widget styling reminiscent of photo editors."""

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1a1d21"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f0f0f0"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#1f2328"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#272b31"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f0f0f0"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#2b2f35"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f0f0f0"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#7aa2ff"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#4d75d4"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#0b0d0f"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2f343a"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#f0f0f0"))
    app.setPalette(palette)

    app.setStyleSheet(
        """
        QMainWindow, QWidget {
            background-color: #1a1d21;
            color: #f0f0f0;
        }

        QToolBar {
            background-color: #23262b;
            border-bottom: 1px solid #2f3339;
        }

        QLabel#appTitleLabel {
            font-size: 16px;
            font-weight: 600;
            color: #f7f7f7;
        }

        QFrame#imageSlot {
            background-color: #202328;
            border: 1px solid #30343a;
            border-radius: 8px;
            padding: 12px;
        }

        QLabel#imageSlotTitle {
            font-size: 14px;
            font-weight: 600;
        }

        QPushButton {
            background-color: #2b2f35;
            color: #f0f0f0;
            border: 1px solid #3a3f46;
            border-radius: 4px;
            padding: 6px 14px;
        }

        QPushButton:hover:!disabled {
            background-color: #34383f;
        }

        QPushButton:pressed {
            background-color: #2a2d33;
        }

        QPushButton:disabled {
            color: #6d7177;
            border-color: #2a2d33;
            background-color: #25282d;
        }
        """
    )
