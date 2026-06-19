"""检测页：视频画布 + 类别筛选 + 实时状态条。

主交互区：实时显示检测标注帧与 ROI 违反红框高亮。
右侧侧栏：类别筛选（实时影响推理）+ 状态读数（FPS / 目标数 / 报警灯）。
"""
from __future__ import annotations

from typing import List

import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QLabel, QFrame,
)

from app.ui.pages.base_page import BasePage
from app.ui.theme import Palette
from app.ui.widgets.video_canvas import VideoCanvas
from app.ui.widgets.class_filter import ClassFilter


class DetectionPage(BasePage):
    title = "检测"

    # 用户改了勾选类别 -> 通知 MainWindow 同步到 detector
    classes_changed = pyqtSignal(list)

    def __init__(
        self,
        classes_meta: dict,
        default_selected: List[int],
        parent: QWidget | None = None,
    ) -> None:
        self._classes_meta = classes_meta
        self._default_selected = list(default_selected)
        super().__init__(parent)

    def _build_content(self) -> None:
        main = QSplitter(Qt.Horizontal)

        # 画布
        self.canvas = VideoCanvas()
        self.canvas.set_palette(self.palette)
        main.addWidget(self.canvas)

        # 右侧侧栏：类别筛选 + 状态条
        sidebar = self._build_sidebar()
        main.addWidget(sidebar)
        main.setStretchFactor(0, 4)
        main.setStretchFactor(1, 1)
        main.setSizes([900, 280])
        self._root_layout.addWidget(main, 1)

    def _build_sidebar(self) -> QWidget:
        panel = QFrame()
        panel.setMaximumWidth(360)
        panel.setMinimumWidth(240)
        v = QVBoxLayout(panel)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(8)

        title_cls = QLabel("识别筛选")
        title_cls.setProperty("role", "title")
        v.addWidget(title_cls)
        self.class_filter = ClassFilter(self._classes_meta, default_selected=self._default_selected)
        self.class_filter.selected_classes.connect(self.classes_changed.emit)
        v.addWidget(self.class_filter, 2)

        title_status = QLabel("实时状态")
        title_status.setProperty("role", "title")
        v.addWidget(title_status)
        status_row = QHBoxLayout()
        self.lbl_fps = QLabel("FPS 0")
        self.lbl_fps.setProperty("role", "data")
        self.lbl_objs = QLabel("目标 0")
        self.lbl_objs.setProperty("role", "data")
        self.lbl_alarm = QLabel("●")
        self.lbl_alarm.setTextFormat(Qt.RichText)
        status_row.addWidget(self.lbl_fps)
        status_row.addWidget(self.lbl_objs)
        status_row.addStretch(1)
        status_row.addWidget(QLabel("报警"))
        status_row.addWidget(self.lbl_alarm)
        v.addLayout(status_row)

        v.addStretch(1)
        return panel

    def _apply_palette(self) -> None:
        self.canvas.set_palette(self.palette)
        self._set_alarm_lamp(self._alarm_on if hasattr(self, "_alarm_on") else False)

    # ---- MainWindow 驱动的数据更新 ----
    def update_frame(self, annotated: np.ndarray, violator_indices, centers) -> None:
        self.canvas.update_frame(annotated)
        self.canvas.set_violators(centers, violator_indices)

    def set_rois(self, rois) -> None:
        self.canvas.set_rois(rois)

    def update_status(self, fps: float, objs: int, alarm: bool) -> None:
        self.lbl_fps.setText(f"FPS {fps:.0f}")
        self.lbl_objs.setText(f"目标 {objs}")
        self._alarm_on = alarm
        self._set_alarm_lamp(alarm)

    def _set_alarm_lamp(self, on: bool) -> None:
        color = self.palette.alarm if on else self.palette.fg_sub
        self.lbl_alarm.setText(f'<span style="color:{color};font-size:18px;">●</span>')

    # ---- MainWindow 需要读取画布信号 ----
    @property
    def roi_created_signal(self):
        """画布 ROI 创建信号（检测页不主动画 ROI，但保留透传以便复用画布交互）。"""
        return self.canvas.roi_created
