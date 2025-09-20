"""Placeholder editor view for early desktop prototype."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class EditorView(QWidget):
    """Temporary placeholder representing the full editor."""

    restart_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        label = QLabel(
            "The advanced editor (manual/automatic pinning, overlays, filters)\n"
            "will be implemented in subsequent iterations."
        )
        label.setWordWrap(True)

        restart_button = QPushButton("Start over")
        restart_button.clicked.connect(self.restart_requested.emit)

        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addSpacing(12)
        layout.addWidget(restart_button)
        layout.addStretch(1)
        self.setLayout(layout)
