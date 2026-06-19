"""控制栏：数据源选择 + 播放控制按钮。"""
from __future__ import annotations

from enum import Enum

from PyQt5.QtCore import QSize, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QComboBox, QLabel, QSpinBox, QFileDialog,
)

from app.ui.theme import get_palette
from app.ui.widgets.svg_icon import load_svg_icon


class SourceType(Enum):
    CAMERA = "camera"
    IMAGE = "image"
    VIDEO = "video"


class ControlBar(QWidget):
    """工具栏控件：源类型切换、源选择、开始/暂停/停止。"""

    start_requested = pyqtSignal(object)   # 发送 (source, SourceType)
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    source_type_changed = pyqtSignal(object)  # SourceType

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_type = SourceType.CAMERA
        self._current_source: object = 0
        self._build()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)
        pal = get_palette()

        # 源类型按钮组
        self.btn_camera = QPushButton("摄像头")
        self.btn_image = QPushButton("图片")
        self.btn_video = QPushButton("视频")
        for b in (self.btn_camera, self.btn_image, self.btn_video):
            b.setCheckable(True)
            b.setProperty("role", "flat")
            b.setIconSize(QSize(16, 16))
        self.btn_camera.setIcon(load_svg_icon("camera", pal.fg_main, 16))
        self.btn_image.setIcon(load_svg_icon("image", pal.fg_main, 16))
        self.btn_video.setIcon(load_svg_icon("video", pal.fg_main, 16))
        self.btn_camera.setChecked(True)
        self.btn_camera.clicked.connect(lambda: self._switch(SourceType.CAMERA))
        self.btn_image.clicked.connect(lambda: self._switch_image())
        self.btn_video.clicked.connect(lambda: self._switch_video())
        layout.addWidget(self.btn_camera)
        layout.addWidget(self.btn_image)
        layout.addWidget(self.btn_video)

        layout.addWidget(self._sep_label("源:"))

        # 源选择
        self.cam_combo = QComboBox()
        for i in range(4):
            self.cam_combo.addItem(f"摄像头 {i}", i)
        self.cam_combo.currentIndexChanged.connect(self._on_cam_changed)
        layout.addWidget(self.cam_combo)

        self.source_label = QLabel("（请选择）")
        self.source_label.setStyleSheet("color: #9AA0A6;")
        self.source_label.hide()
        layout.addWidget(self.source_label)

        layout.addStretch(1)

        # 播放控制
        self.btn_start = QPushButton("开始")
        self.btn_pause = QPushButton("暂停")
        self.btn_stop = QPushButton("停止")
        for b in (self.btn_start, self.btn_pause, self.btn_stop):
            b.setIconSize(QSize(16, 16))
        self.btn_start.setIcon(load_svg_icon("start", "#FFFFFF", 16))
        self.btn_pause.setIcon(load_svg_icon("pause", "#FFFFFF", 16))
        self.btn_stop.setIcon(load_svg_icon("stop", "#FFFFFF", 16))
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_pause.clicked.connect(self._on_pause)
        self.btn_stop.clicked.connect(self.stop_requested.emit)
        layout.addWidget(self.btn_start)
        layout.addWidget(self.btn_pause)
        layout.addWidget(self.btn_stop)

    def _sep_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #9AA0A6;")
        return lbl

    # ---- 源类型切换 ----
    def _switch(self, src_type: SourceType) -> None:
        self._current_type = src_type
        for b, t in (
            (self.btn_camera, SourceType.CAMERA),
            (self.btn_image, SourceType.IMAGE),
            (self.btn_video, SourceType.VIDEO),
        ):
            b.setChecked(t == src_type)
        # 显示对应源选择控件
        self.cam_combo.setVisible(src_type == SourceType.CAMERA)
        self.source_label.setVisible(src_type != SourceType.CAMERA)
        if src_type == SourceType.CAMERA:
            self._current_source = self.cam_combo.currentData()
        else:
            self._current_source = None
            self.source_label.setText("（请选择）")
        self.source_type_changed.emit(src_type)

    def _switch_image(self) -> None:
        self._switch(SourceType.IMAGE)
        path, _ = QFileDialog.getOpenFileName(self, "选择图片", "", "Images (*.jpg *.jpeg *.png *.bmp)")
        if path:
            self._current_source = path
            self.source_label.setText(self._short(path))

    def _switch_video(self) -> None:
        self._switch(SourceType.VIDEO)
        path, _ = QFileDialog.getOpenFileName(self, "选择视频", "", "Videos (*.mp4 *.avi *.mov *.mkv)")
        if path:
            self._current_source = path
            self.source_label.setText(self._short(path))

    @staticmethod
    def _short(path: str, n: int = 40) -> str:
        return path if len(path) <= n else "..." + path[-(n - 3):]

    def _on_cam_changed(self) -> None:
        if self._current_type == SourceType.CAMERA:
            self._current_source = self.cam_combo.currentData()

    # ---- 播放控制 ----
    def _on_start(self) -> None:
        if self._current_source is None:
            return
        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.start_requested.emit((self._current_source, self._current_type))

    def _on_pause(self) -> None:
        if self.btn_pause.text() == "暂停":
            self.btn_pause.setText("继续")
            self.pause_requested.emit()
        else:
            self.btn_pause.setText("暂停")
            self.resume_requested.emit()

    def on_stopped(self) -> None:
        """外部停止后重置按钮状态。"""
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("暂停")
        self.btn_stop.setEnabled(False)

    @property
    def current_source(self):
        return self._current_source

    @property
    def current_type(self) -> SourceType:
        return self._current_type
