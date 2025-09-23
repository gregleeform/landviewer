"""Application bootstrap helpers."""
from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from landviewer_desktop.main_window import MainWindow


def run() -> None:
    """Create the Qt event loop and show the main window."""
    app = QApplication(sys.argv)
    _apply_light_theme(app)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def _apply_light_theme(app: QApplication) -> None:
    """Apply a light grey palette to give the editor a neutral photo-tool look."""

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#edf1f5"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#1f2933"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#f8fafc"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#e3e8ef"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#1f2933"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#dbe4f0"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#1f2933"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#2563eb"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#93c5fd"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#1f2933"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#111827"))
    app.setPalette(palette)

    app.setStyleSheet(
        """
        QMainWindow, QWidget {
            background-color: #edf1f5;
            color: #1f2933;
        }

        QToolBar {
            background-color: #d9e0ea;
            border-bottom: 1px solid #c7d0dd;
        }

        QLabel#appTitleLabel {
            font-size: 16px;
            font-weight: 600;
            color: #1f2933;
        }

        QFrame#imageSlot {
            background-color: #f8fafc;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            padding: 12px;
        }

        QLabel#imageSlotTitle {
            font-size: 14px;
            font-weight: 600;
        }

        QLabel#imageSlotFilename {
            font-size: 12px;
            color: #475569;
        }

        QLabel#cropSelectionLabel {
            font-size: 12px;
            color: #475569;
        }

        QLabel#editorInfoLabel {
            font-size: 12px;
            color: #475569;
        }

        QLabel#overlayOpacityValue {
            font-size: 12px;
            color: #475569;
            min-width: 48px;
        }

        QGraphicsView {
            border: 1px solid #cbd5e1;
            background-color: #f3f4f6;
        }

        QPushButton {
            background-color: #dbe4f0;
            color: #1f2933;
            border: 1px solid #bac4d1;
            border-radius: 4px;
            padding: 6px 14px;
        }

        QPushButton:hover:!disabled {
            background-color: #cfd9e6;
        }

        QPushButton:pressed {
            background-color: #c3cedd;
        }

        QPushButton:disabled {
            color: #94a3b8;
            border-color: #d1d9e4;
            background-color: #edf1f5;
        }
        """
    )
