"""Placeholder crop view that will host the interactive cropper."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class CropView(QWidget):
    """Temporary placeholder for the crop workflow."""

    back_requested = Signal()
    proceed_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        label = QLabel(
            "Crop tooling is not yet implemented.\n"
            "This placeholder allows you to navigate the prototype screens."
        )
        label.setWordWrap(True)

        back_button = QPushButton("Back to upload")
        continue_button = QPushButton("Continue to editor")

        back_button.clicked.connect(self.back_requested.emit)
        continue_button.clicked.connect(self.proceed_requested.emit)

        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addSpacing(12)
        layout.addWidget(back_button)
        layout.addWidget(continue_button)
        layout.addStretch(1)
        self.setLayout(layout)
