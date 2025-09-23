"""Reusable widget representing an image slot in the upload screen."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class ImageSlotWidget(QFrame):
    """Widget that lets the user choose or clear an image."""

    select_requested = Signal()
    clear_requested = Signal()

    def __init__(self, title: str, description: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._title_label = QLabel(title)
        self._title_label.setObjectName("imageSlotTitle")
        self._description_label = QLabel(description)
        self._description_label.setWordWrap(True)
        self._preview_label = QLabel("No image selected")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumHeight(320)
        self._preview_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._preview_label.setStyleSheet(
            "QLabel { border: 1px dashed #94a3b8; padding: 12px; color: #64748b; }"
        )

        self._filename_label = QLabel("")
        self._filename_label.setObjectName("imageSlotFilename")
        self._filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._filename_label.setWordWrap(True)

        self._select_button = QPushButton("Choose image…")
        self._clear_button = QPushButton("Remove")
        self._clear_button.setEnabled(False)

        self._select_button.clicked.connect(self.select_requested.emit)
        self._clear_button.clicked.connect(self.clear_requested.emit)

        button_row = QHBoxLayout()
        button_row.addWidget(self._select_button)
        button_row.addWidget(self._clear_button)
        button_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self._title_label)
        layout.addWidget(self._description_label)
        layout.addSpacing(4)
        layout.addWidget(self._preview_label, stretch=1)
        layout.addSpacing(6)
        layout.addWidget(self._filename_label)
        layout.addSpacing(8)
        layout.addLayout(button_row)
        self.setLayout(layout)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("imageSlot")

        self._source_pixmap: Optional[QPixmap] = None

    def set_preview(self, pixmap: QPixmap, filename: str) -> None:
        """Show a preview pixmap and update button state."""
        self._source_pixmap = pixmap
        self._preview_label.setText("")
        self._update_preview_pixmap()
        self._filename_label.setText(filename)
        self._preview_label.setToolTip(filename)
        self._clear_button.setEnabled(True)

    def clear_preview(self) -> None:
        """Reset the widget to its empty state."""
        self._source_pixmap = None
        self._preview_label.setPixmap(QPixmap())
        self._preview_label.setText("No image selected")
        self._preview_label.setToolTip("")
        self._filename_label.setText("")
        self._clear_button.setEnabled(False)

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._update_preview_pixmap()

    # ------------------------------------------------------------------
    def _update_preview_pixmap(self) -> None:
        if not self._source_pixmap:
            return
        contents_size = self._preview_label.contentsRect().size()
        if contents_size.width() <= 0 or contents_size.height() <= 0:
            contents_size = self._preview_label.size()
        contents_size.setWidth(max(contents_size.width(), 1))
        contents_size.setHeight(max(contents_size.height(), 1))
        scaled = self._source_pixmap.scaled(
            contents_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview_label.setPixmap(scaled)
