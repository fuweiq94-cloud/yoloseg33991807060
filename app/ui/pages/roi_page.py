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


class RoiPage(BasePage):
    title = "ROI 区域"

    # 用户操作 -> MainWindow
    new_roi_requested = pyqtSignal()
    clear_roi_requested = pyqtSignal()
    roi_created = pyqtSignal(list)        # 画布点选闭合 -> 透传顶点（帧像素坐标）
    import_requested = pyqtSignal()
    export_requested = pyqtSignal()

    def _build_content(self) -> None:
        tip = QLabel("点击「新建多边形」后，在画面上单击添加顶点，双击或回车闭合，ESC 取消。")
        tip.setProperty("role", "sub")
        tip.setWordWrap(True)
        self._root_layout.addWidget(tip)

        main = QSplitter(Qt.Horizontal)

        # 独立画布
        self.canvas = VideoCanvas()
        self.canvas.set_palette(self.palette)
        self.canvas.roi_created.connect(self.roi_created.emit)
        main.addWidget(self.canvas)

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
        self.btn_new.clicked.connect(self._on_new_clicked)
        self.btn_clear = QPushButton("清除全部")
        self.btn_clear.setProperty("role", "flat")
        self.btn_clear.clicked.connect(self.clear_roi_requested.emit)
        row.addWidget(self.btn_new)
        row.addWidget(self.btn_clear)
        v.addLayout(row)

        row2 = QHBoxLayout()
        self.btn_import = QPushButton("导入")
        self.btn_import.setProperty("role", "flat")
        self.btn_import.clicked.connect(self.import_requested.emit)
        self.btn_export = QPushButton("导出")
        self.btn_export.setProperty("role", "flat")
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

    def set_rois(self, rois) -> None:
        self.canvas.set_rois(rois)

    def refresh_roi_list(self, regions) -> None:
        """regions: list[RoiRegion]。"""
        self.roi_list.clear()
        for r in regions:
            self.roi_list.addItem(f"{r.label}  ({len(r.points)} 点)")
