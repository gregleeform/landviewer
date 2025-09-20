"""Main window wiring together all prototype screens."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QMainWindow, QSizePolicy, QStackedWidget, QToolBar, QWidget

from landviewer_desktop.state import AppStage, AppState
from landviewer_desktop.views.crop_view import CropView
from landviewer_desktop.views.editor_view import EditorView
from landviewer_desktop.views.upload_view import UploadView


class MainWindow(QMainWindow):
    """Root window that manages navigation and shared state."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Landviewer Desktop Prototype")
        self.resize(1200, 800)

        self._state = AppState()

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._upload_view = UploadView(self._state)
        self._crop_view = CropView(self._state)
        self._editor_view = EditorView()

        self._stack.addWidget(self._upload_view)
        self._stack.addWidget(self._crop_view)
        self._stack.addWidget(self._editor_view)

        self._stage_to_widget = {
            AppStage.UPLOAD: self._upload_view,
            AppStage.CROP: self._crop_view,
            AppStage.EDIT: self._editor_view,
        }

        self._build_toolbar()
        self._connect_signals()
        self._upload_view.refresh()
        self._set_stage(AppStage.UPLOAD)

    # ------------------------------------------------------------------
    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main controls", self)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setAllowedAreas(Qt.ToolBarArea.TopToolBarArea)
        title = QLabel("Landviewer Desktop Prototype")
        title.setObjectName("appTitleLabel")
        toolbar.addWidget(title)
        toolbar.addSeparator()

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self._start_over_action = QAction("Start over", self)
        self._start_over_action.triggered.connect(self._handle_restart)
        toolbar.addAction(self._start_over_action)
        self._start_over_action.setVisible(False)

        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        self._toolbar = toolbar

    def _connect_signals(self) -> None:
        self._upload_view.proceed_requested.connect(lambda: self._set_stage(AppStage.CROP))
        self._crop_view.back_requested.connect(lambda: self._set_stage(AppStage.UPLOAD))
        self._crop_view.proceed_requested.connect(lambda: self._set_stage(AppStage.EDIT))
        self._editor_view.restart_requested.connect(self._handle_restart)

    def _set_stage(self, stage: AppStage) -> None:
        """Update current stage and show the corresponding widget."""
        self._state.stage = stage
        widget = self._stage_to_widget[stage]
        self._stack.setCurrentWidget(widget)

        if stage is AppStage.UPLOAD:
            self._upload_view.refresh()
            self._start_over_action.setVisible(False)
        elif stage is AppStage.CROP:
            self._crop_view.refresh()
            self._start_over_action.setVisible(True)
        else:
            self._start_over_action.setVisible(True)

    def _handle_restart(self) -> None:
        """Reset state and navigate back to the upload screen."""
        self._state.reset()
        self._upload_view.refresh()
        self._set_stage(AppStage.UPLOAD)
