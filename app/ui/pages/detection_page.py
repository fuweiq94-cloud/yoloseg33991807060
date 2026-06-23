"""检测页：视频画布 + 类别筛选 + 实时状态条。

主交互区：实时显示检测标注帧与 ROI 违反红框高亮。
右侧侧栏：类别筛选（实时影响推理）+ 状态读数（FPS / 目标数 / 报警灯）。
视频文件源时，画布下方出现内联播放控件（播放/暂停 + 可拖动进度条）。
"""
from __future__ import annotations

from typing import List

import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView,
)

from app.ui.pages.base_page import BasePage
from app.ui.theme import Palette
from app.ui.widgets.video_canvas import VideoCanvas
from app.ui.widgets.video_playback_bar import VideoPlaybackBar
from app.ui.widgets.class_filter import ClassFilter


class DetectionPage(BasePage):
    title = "检测"
    icon_name = "detection"

    # 用户改了勾选类别 -> 通知 MainWindow 同步到 detector
    classes_changed = pyqtSignal(list)
    # 用户改了报警类别勾选 -> 通知 MainWindow 同步到 roi_manager（按类过滤报警）
    alarm_classes_changed = pyqtSignal(list)
    # 视频内联播放控件信号（透传给 MainWindow）
    video_play_toggled = pyqtSignal()
    video_seek_requested = pyqtSignal(int)

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

        # 左侧：画布容器 + 视频内联播放控件（叠在画布下方）
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        # 画布容器：单源模式放 1 个画布，多源模式切换为 2x2 网格。
        # self.canvas 始终指向 source_id=0 的主画布，保证单源逻辑（update_frame/set_rois）不变。
        self._canvas_container = QWidget()
        self._canvas_layout = QVBoxLayout(self._canvas_container)
        self._canvas_layout.setContentsMargins(0, 0, 0, 0)
        self._canvas_layout.setSpacing(2)
        self._multi_mode = False
        self.canvases: list[VideoCanvas] = []   # 多源模式：4 个画布（按 source_id 索引）
        self.canvas = VideoCanvas()             # 单源主画布（= 多源 canvases[0]）
        self.canvas.set_palette(self.palette)
        self._canvas_layout.addWidget(self.canvas, 1)
        left_layout.addWidget(self._canvas_container, 1)
        self.playback_bar = VideoPlaybackBar()
        self.playback_bar.hide()  # 仅视频文件源显示
        self.playback_bar.play_toggled.connect(self.video_play_toggled.emit)
        self.playback_bar.seek_requested.connect(self.video_seek_requested.emit)
        left_layout.addWidget(self.playback_bar)
        main.addWidget(left)

        # 右侧侧栏：类别筛选 + 状态条
        sidebar = self._build_sidebar()
        main.addWidget(sidebar)
        # 第三段：目标详情面板（逐类别计数 + 目标清单）
        details = self._build_details_panel()
        main.addWidget(details)
        main.setStretchFactor(0, 4)   # 画布
        main.setStretchFactor(1, 1)   # 侧栏
        main.setStretchFactor(2, 1)   # 详情
        main.setSizes([820, 260, 240])
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

        title_alarm = QLabel("报警类别")
        title_alarm.setProperty("role", "title")
        v.addWidget(title_alarm)
        # 报警类别：默认全选（向后兼容）。仅控制"哪些类别进 ROI 才报警"，
        # 不影响检测/红框高亮/统计。tip 提示语义区别，避免与「识别筛选」混淆。
        hint = QLabel("仅勾选类别进入 ROI 才报警（检测与红框不受影响）")
        hint.setProperty("role", "sub")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.alarm_filter = ClassFilter(self._classes_meta, default_selected=None)
        self.alarm_filter.selected_classes.connect(self.alarm_classes_changed.emit)
        v.addWidget(self.alarm_filter, 2)

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

    def _build_details_panel(self) -> QWidget:
        """目标详情面板：顶部逐类别计数汇总 + 下方目标清单（序号/ID/类别/置信度）。

        数据由 MainWindow.update_details 1Hz 推送（每帧重建表格对 GUI 太重）。
        """
        panel = QFrame()
        panel.setMaximumWidth(360)
        panel.setMinimumWidth(200)
        v = QVBoxLayout(panel)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(8)

        title = QLabel("目标详情")
        title.setProperty("role", "title")
        v.addWidget(title)

        # 逐类别计数汇总（内联文本，无目标时显示「无目标」）
        self.lbl_counts = QLabel("无目标")
        self.lbl_counts.setProperty("role", "sub")
        self.lbl_counts.setWordWrap(True)
        v.addWidget(self.lbl_counts)

        # 目标清单表格：序号 / 跟踪ID / 类别 / 置信度
        self.tbl_targets = QTableWidget(0, 4)
        self.tbl_targets.setHorizontalHeaderLabels(["序号", "ID", "类别", "置信度"])
        self.tbl_targets.verticalHeader().setVisible(False)          # 隐藏行号
        self.tbl_targets.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_targets.setSelectionMode(QAbstractItemView.NoSelection)
        self.tbl_targets.setFocusPolicy(Qt.NoFocus)                  # 不抢画布焦点
        hdr = self.tbl_targets.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        v.addWidget(self.tbl_targets, 1)

        return panel

    def _apply_palette(self) -> None:
        self.canvas.set_palette(self.palette)
        self._set_alarm_lamp(self._alarm_on if hasattr(self, "_alarm_on") else False)

    # ---- MainWindow 驱动的数据更新 ----
    def update_frame(self, annotated: np.ndarray, violator_indices, centers) -> None:
        self.canvas.update_frame(annotated)
        self.canvas.set_violators(centers, violator_indices)

    # ---- 多源模式 ----
    def set_multi_mode(self, enabled: bool) -> None:
        """切换单/多源画布布局。多源：2x2 网格 4 个画布；单源：1 个主画布。

        单源逻辑（update_frame/set_rois 等）始终操作 self.canvas，多源时它等于
        canvases[0]，故单源路径在多源模式下也正常更新主路画面。
        """
        if enabled == self._multi_mode:
            return
        self._multi_mode = enabled
        # 清空容器现有内容
        while self._canvas_layout.count():
            it = self._canvas_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
        if enabled:
            # 多源：2x2 网格 4 个画布
            from PyQt5.QtWidgets import QGridLayout
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(2)
            self.canvases = []
            for i in range(4):
                c = VideoCanvas()
                c.set_palette(self.palette)
                c.show_placeholder_text(f"摄像头 {i}（未启用）")
                self.canvases.append(c)
                grid.addWidget(c, i // 2, i % 2)
            self.canvas = self.canvases[0]  # 主路画布
            wrap = QWidget()
            wrap.setLayout(grid)
            self._canvas_layout.addWidget(wrap, 1)
        else:
            # 单源：恢复 1 个主画布
            self.canvases = []
            self.canvas = VideoCanvas()
            self.canvas.set_palette(self.palette)
            self._canvas_layout.addWidget(self.canvas, 1)

    def update_frame_multi(self, source_id: int, annotated: np.ndarray,
                           violator_indices, centers) -> None:
        """多源帧更新：按 source_id 路由到对应画布。"""
        if 0 <= source_id < len(self.canvases):
            c = self.canvases[source_id]
            c.update_frame(annotated)
            c.set_violators(centers, violator_indices)

    def mark_source_inactive(self, source_id: int) -> None:
        """某路采集结束：在该画布显示无信号占位。"""
        if 0 <= source_id < len(self.canvases):
            self.canvases[source_id].show_placeholder_text(f"摄像头 {source_id}（已断开）")

    # ---- 视频内联播放控件 ----
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

    def set_alarm_classes(self, ids) -> None:
        """从配置恢复报警类别勾选。ids 为 None/空 = 全选（向后兼容）。"""
        if ids:
            self.alarm_filter.set_selected([int(i) for i in ids])
        else:
            # None = 全报警：勾选全部（ClassFilter 默认即全选，这里显式补全）
            self.alarm_filter.set_selected([int(k) for k in self._classes_meta.keys()])

    def update_status(self, fps: float, objs: int, alarm: bool) -> None:
        self.lbl_fps.setText(f"FPS {fps:.0f}")
        self.lbl_objs.setText(f"目标 {objs}")
        self._alarm_on = alarm
        self._set_alarm_lamp(alarm)

    def update_details(self, details) -> None:
        """刷新目标详情面板。details 为 FrameDetails 或 None。

        由 MainWindow 1Hz 节流调用。无目标/停止时清空表格、汇总显示「无目标」。
        表格不排序，保持与画布检测框同序，便于对照。
        """
        targets = getattr(details, "targets", []) if details is not None else []
        counts = getattr(details, "counts", {}) if details is not None else {}

        # 逐类别计数汇总：如「人 3 · 车 2」，按数量降序
        if counts:
            parts = [f"{name} {n}" for name, n in
                     sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]
            self.lbl_counts.setText(" · ".join(parts))
        else:
            self.lbl_counts.setText("无目标")

        # 目标清单：行数=目标数，逐行填 [序号, ID, 类别, 置信度]
        self.tbl_targets.setRowCount(len(targets))
        for row, t in enumerate(targets):
            # ID：图片源（track_id<0）显示「-」
            tid = str(t.track_id) if t.track_id >= 0 else "-"
            self._set_cell(row, 0, str(row + 1))
            self._set_cell(row, 1, tid)
            self._set_cell(row, 2, t.cls_name)
            self._set_cell(row, 3, f"{t.conf:.2f}")

    def _set_cell(self, row: int, col: int, text: str) -> None:
        """填一格并右对齐数字列（序号/ID/置信度），类别列左对齐。"""
        item = QTableWidgetItem(text)
        if col != 2:  # 类别列(col=2)默认左对齐，其余右对齐
            item.setTextAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self.tbl_targets.setItem(row, col, item)

    def _set_alarm_lamp(self, on: bool) -> None:
        color = self.palette.alarm if on else self.palette.fg_sub
        self.lbl_alarm.setText(f'<span style="color:{color};font-size:18px;">●</span>')

    # ---- MainWindow 需要读取画布信号 ----
    @property
    def roi_created_signal(self):
        """画布 ROI 创建信号（检测页不主动画 ROI，但保留透传以便复用画布交互）。"""
        return self.canvas.roi_created
