"""ROI 页：独立画布用于在画面上点选多边形 + ROI 操作按钮与列表。

持有自己的 VideoCanvas 实例，与检测页画布共享同一视频源
（MainWindow 把 worker 的 frame_ready 同时推给两个画布）。
ROI 绘制、导入/导出等用户操作通过信号回传 MainWindow 处理。
"""
from __future__ import annotations

import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QLabel, QFrame,
    QPushButton, QListWidget,
)

from app.ui.pages.base_page import BasePage
from app.ui.widgets.video_canvas import VideoCanvas
from app.ui.widgets.video_playback_bar import VideoPlaybackBar
from app.ui.widgets.svg_icon import load_svg_icon


class RoiPage(BasePage):
    title = "ROI 区域"
    icon_name = "roi"

    # 用户操作 -> MainWindow
    new_roi_requested = pyqtSignal()
    clear_roi_requested = pyqtSignal()
    roi_created = pyqtSignal(list)        # 画布点选闭合 -> 透传顶点（帧像素坐标）
    import_requested = pyqtSignal()
    export_requested = pyqtSignal()
    # 视频内联播放控件信号（透传给 MainWindow，与检测页一致）
    video_play_toggled = pyqtSignal()
    video_seek_requested = pyqtSignal(int)

    def _build_content(self) -> None:
        tip = QLabel("点击「新建多边形」后，在画面上单击添加顶点，双击或回车闭合，ESC 取消。")
        tip.setProperty("role", "sub")
        tip.setWordWrap(True)
        self._root_layout.addWidget(tip)

        main = QSplitter(Qt.Horizontal)

        # 左侧：独立画布 + 视频内联播放控件
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        self.canvas = VideoCanvas()
        self.canvas.set_palette(self.palette)
        self.canvas.roi_created.connect(self.roi_created.emit)
        left_layout.addWidget(self.canvas, 1)
        self.playback_bar = VideoPlaybackBar()
        self.playback_bar.hide()  # 仅视频文件源显示
        self.playback_bar.play_toggled.connect(self.video_play_toggled.emit)
        self.playback_bar.seek_requested.connect(self.video_seek_requested.emit)
        left_layout.addWidget(self.playback_bar)
        main.addWidget(left)

        # 侧栏：操作 + 列表
        sidebar = self._build_sidebar()
        main.addWidget(sidebar)
        main.setStretchFactor(0, 4)
        main.setStretchFactor(1, 1)
        main.setSizes([900, 260])
        self._root_layout.addWidget(main, 1)

    def _build_sidebar(self) -> QWidget:
        panel = QFrame()
        panel.setMaximumWidth(320)
        panel.setMinimumWidth(220)
        v = QVBoxLayout(panel)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(8)

        title = QLabel("ROI 管理")
        title.setProperty("role", "title")
        v.addWidget(title)

        row = QHBoxLayout()
        self.btn_new = QPushButton("新建多边形")
        self.btn_new.setProperty("role", "flat")
        self.btn_new.setIcon(load_svg_icon("polygon_add", self.palette.fg_main, 16))
        self.btn_new.clicked.connect(self._on_new_clicked)
        self.btn_clear = QPushButton("清除全部")
        self.btn_clear.setProperty("role", "flat")
        self.btn_clear.setIcon(load_svg_icon("trash", self.palette.fg_main, 16))
        self.btn_clear.clicked.connect(self.clear_roi_requested.emit)
        row.addWidget(self.btn_new)
        row.addWidget(self.btn_clear)
        v.addLayout(row)

        row2 = QHBoxLayout()
        self.btn_import = QPushButton("导入")
        self.btn_import.setProperty("role", "flat")
        self.btn_import.setIcon(load_svg_icon("import", self.palette.fg_main, 16))
        self.btn_import.clicked.connect(self.import_requested.emit)
        self.btn_export = QPushButton("导出")
        self.btn_export.setProperty("role", "flat")
        self.btn_export.setIcon(load_svg_icon("export", self.palette.fg_main, 16))
        self.btn_export.clicked.connect(self.export_requested.emit)
        row2.addWidget(self.btn_import)
        row2.addWidget(self.btn_export)
        v.addLayout(row2)

        v.addWidget(QLabel("已建区域"))
        self.roi_list = QListWidget()
        v.addWidget(self.roi_list, 1)

        return panel

    def _on_new_clicked(self) -> None:
        # 进入画布绘制模式，由画布自身处理点选与闭合
        self.canvas.start_drawing()
        self.canvas.setFocus()
        self.new_roi_requested.emit()

    def _apply_palette(self) -> None:
        self.canvas.set_palette(self.palette)

    # ---- MainWindow 驱动的数据更新 ----
    def update_frame(self, annotated: np.ndarray, violator_indices, centers) -> None:
        # ROI 页同样显示当前画面，便于对照绘制
        self.canvas.update_frame(annotated)
        self.canvas.set_violators(centers, violator_indices)

    # ---- 视频内联播放控件（与检测页一致）----
    def set_video_mode(self, enabled: bool, frame_count: int = 0, fps: float = 0.0) -> None:
        """启用/禁用视频内联播放控件。仅视频文件源启用。"""
        self.playback_bar.setVisible(enabled)
        if enabled:
            self.playback_bar.set_range(frame_count, fps)
            self.playback_bar.reset()

    def set_video_position(self, frame_idx: int) -> None:
        """worker 推进时更新进度条位置。"""
        if self.playback_bar.isVisible():
            self.playback_bar.set_position(frame_idx)

    def set_video_playing(self, playing: bool) -> None:
        """播放/暂停状态回灌（与顶部 ControlBar 同步）。"""
        if self.playback_bar.isVisible():
            self.playback_bar.set_playing(playing)

    def set_rois(self, rois) -> None:
        self.canvas.set_rois(rois)

    def refresh_roi_list(self, regions) -> None:
        """regions: list[RoiRegion]。每项显示颜色块 + 标签 + 面积。"""
        from PyQt5.QtGui import QPixmap, QPainter, QColor
        from PyQt5.QtCore import Qt, QSize
        from PyQt5.QtWidgets import QListWidgetItem
        self.roi_list.clear()
        self.roi_list.setIconSize(QSize(14, 14))
        for r in regions:
            # 颜色块图标
            color = r.color or "#3b82f6"
            pm = QPixmap(14, 14)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setBrush(QColor(color))
            p.setPen(QColor(color))
            p.drawRoundedRect(1, 1, 12, 12, 3, 3)
            p.end()
            # 面积格式化：像素²，大数值用 k/m
            area = getattr(r, "area", 0.0)
            area_str = self._fmt_area(area)
            item = QListWidgetItem(f"{r.label}    {len(r.points)} 点    面积 {area_str}")
            item.setIcon(QPixmap(pm))
            self.roi_list.addItem(item)

    @staticmethod
    def _fmt_area(area: float) -> str:
        """面积（像素²）格式化：大数值用 k/m 降量级。"""
        if area <= 0:
            return "0"
        if area >= 1_000_000:
            return f"{area/1_000_000:.2f}M px²"
        if area >= 1_000:
            return f"{area/1_000:.1f}k px²"
        return f"{int(area)} px²"
