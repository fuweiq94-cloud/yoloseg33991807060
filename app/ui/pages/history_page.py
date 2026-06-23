"""历史记录页：用 Tab 区分图片/视频，缩略图网格展示，点击在右侧内嵌预览。

数据由 HistoryManager 提供（MainWindow 注入）。本页只负责展示与交互：
- 顶部 Tab：「图片」/「视频」分开查看，不混在一起
- 左右分栏：左侧缩略图网格，右侧内嵌预览面板（图片查看 / 视频播放）
  双击/右键打开记录后，在右侧同屏预览，不再弹独立窗口
- 右键菜单：删除单条
- 顶部按钮：刷新 / 清空全部
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtWidgets import (
    QListWidget, QListWidgetItem, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QWidget, QMenu, QAction, QAbstractItemView, QMessageBox,
    QTabWidget, QSplitter, QComboBox, QLineEdit, QCheckBox,
)

from app.ui.pages.base_page import BasePage
from app.ui.theme import Palette
from app.ui.widgets.history_preview_pane import HistoryPreviewPane
from app.ui.widgets.svg_icon import load_svg_icon
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.core.history import HistoryManager, HistoryRecord

logger = get_logger()

THUMB_W, THUMB_H = 200, 130  # 网格缩略图尺寸（含文字区）
PAGE_SIZE = 50               # 每页显示记录数
TIME_RANGES = ["全部", "今日", "近三天", "近七天"]


class _HistoryGrid(QListWidget):
    """单个类型的缩略图网格（图片或视频）。"""

    def __init__(self, history: "HistoryManager", page: "HistoryPage", parent=None) -> None:
        super().__init__(parent)
        self._history = history
        self._page = page
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(THUMB_W, THUMB_H))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setSpacing(8)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setUniformItemSizes(True)
        self.itemDoubleClicked.connect(self._on_activated)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def load(self, records: "list[HistoryRecord]") -> None:
        self.clear()
        for rec in records:
            item = QListWidgetItem()
            item.setText(f"{rec.label()}\n{rec.time_str()}\n{rec.sublabel()}")
            item.setTextAlignment(Qt.AlignCenter)
            pm = self._page.load_thumb(rec)
            if pm is not None:
                item.setIcon(QIcon(pm))
            if rec.type == "video":
                item.setToolTip(f"视频 · {rec.duration:.1f}s\n{rec.time_str()}")
            else:
                item.setToolTip(f"图片\n{rec.time_str()}")
            item.setData(Qt.UserRole, rec.id)
            self.addItem(item)

    def _on_activated(self, item: QListWidgetItem) -> None:
        record_id = item.data(Qt.UserRole)
        record = self._history.get(record_id)
        if record is None:
            return
        self._page.open_record(record)

    def _on_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        act_open = QAction("打开", self)
        act_del = QAction("删除", self)
        menu.addAction(act_open)
        menu.addAction(act_del)
        action = menu.exec_(self.mapToGlobal(pos))
        if action is act_open:
            self._on_activated(item)
        elif action is act_del:
            record_id = item.data(Qt.UserRole)
            reply = QMessageBox.question(
                self, "删除", "确定删除这条历史记录？文件也会被删除。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._history.delete(record_id)
                self._page.refresh()
                self._page._preview.show_empty()


class HistoryPage(BasePage):
    """历史记录页：图片/视频分 Tab 展示。"""

    title = "历史记录"
    icon_name = "history"

    def __init__(self, parent: QWidget | None = None) -> None:
        self._history: "HistoryManager | None" = None
        self._grid_image: _HistoryGrid | None = None
        self._grid_video: _HistoryGrid | None = None
        self._tabs: QTabWidget | None = None
        self._count_label: QLabel | None = None
        self._empty_hint: QLabel | None = None
        # 筛选/分页状态（会话级，不持久化）
        self._page: int = 1            # 当前页码（1 起）
        self._filtered_total: int = 0  # 当前筛选条件下当前 Tab 的总匹配数
        super().__init__(parent)

    def set_history_manager(self, history: "HistoryManager") -> None:
        self._history = history
        self.refresh()

    def _build_content(self) -> None:
        layout = QVBoxLayout()
        layout.setSpacing(6)

        # 工具栏
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._btn_refresh = QPushButton("刷新")
        self._btn_clear = QPushButton("清空全部")
        for b in (self._btn_refresh, self._btn_clear):
            b.setProperty("role", "flat")
        self._btn_refresh.setIcon(load_svg_icon("refresh", self._palette.fg_main, 16))
        self._btn_clear.setIcon(load_svg_icon("trash", self._palette.alarm, 16))
        self._btn_refresh.clicked.connect(self.refresh)
        self._btn_clear.clicked.connect(self._on_clear_all)
        self._count_label = QLabel("0 条记录")
        self._count_label.setStyleSheet(f"color: {self._palette.fg_sub};")
        bar.addWidget(self._btn_refresh)
        bar.addWidget(self._btn_clear)
        bar.addStretch(1)
        bar.addWidget(self._count_label)
        layout.addLayout(bar)

        # 筛选行：仅看报警 + 时间范围 + 搜索框
        filt = QHBoxLayout()
        filt.setSpacing(8)
        self._chk_alarm = QCheckBox("仅看报警")
        self._chk_alarm.stateChanged.connect(self._on_filter_changed)
        filt.addWidget(self._chk_alarm)
        filt.addWidget(QLabel("时间:"))
        self._cmb_time = QComboBox()
        self._cmb_time.addItems(TIME_RANGES)
        self._cmb_time.currentIndexChanged.connect(self._on_filter_changed)
        filt.addWidget(self._cmb_time)
        filt.addWidget(QLabel("搜索:"))
        self._edt_search = QLineEdit()
        self._edt_search.setPlaceholderText("搜索来源/类别")
        self._edt_search.setClearButtonEnabled(True)
        self._edt_search.textChanged.connect(self._on_filter_changed)
        filt.addWidget(self._edt_search, 1)
        layout.addLayout(filt)

        # 空状态提示
        self._empty_hint = QLabel("暂无历史记录\n\n进行图片/视频识别后点击「保存」按钮，结果会出现在这里")
        self._empty_hint.setAlignment(Qt.AlignCenter)
        self._empty_hint.setProperty("role", "sub")
        self._empty_hint.setStyleSheet(f"color: {self._palette.fg_sub}; padding: 60px;")

        # 主体：左侧 Tab 网格 + 右侧内嵌预览面板（不再弹独立窗口）
        # 左侧用一个容器包住 tabs + 分页控件，分页控件贴在网格下方
        self._left_panel = QWidget()
        left_layout = QVBoxLayout(self._left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        self._splitter = QSplitter(Qt.Horizontal)
        self._tabs = QTabWidget()
        self._tabs.currentChanged.connect(self._on_tab_changed)
        # 右侧预览面板：双击左侧记录后在此处查看图片/播放视频
        self._preview = HistoryPreviewPane()
        self._splitter.addWidget(self._left_panel)
        self._splitter.addWidget(self._preview)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 4)
        self._splitter.setSizes([520, 720])
        left_layout.addWidget(self._tabs, 1)

        # 分页控件
        pager = QHBoxLayout()
        pager.addStretch(1)
        self._btn_prev = QPushButton("上一页")
        self._btn_prev.setProperty("role", "flat")
        self._btn_prev.clicked.connect(self._on_prev_page)
        self._lbl_page = QLabel("第 1 / 1 页")
        self._lbl_page.setStyleSheet(f"color: {self._palette.fg_sub};")
        self._btn_next = QPushButton("下一页")
        self._btn_next.setProperty("role", "flat")
        self._btn_next.clicked.connect(self._on_next_page)
        pager.addWidget(self._btn_prev)
        pager.addWidget(self._lbl_page)
        pager.addWidget(self._btn_next)
        pager.addStretch(1)
        left_layout.addLayout(pager)

        layout.addWidget(self._empty_hint)
        layout.addWidget(self._splitter, 1)

        self._root_layout.addLayout(layout, 1)

    def _ensure_grids(self) -> None:
        """懒构建两个 grid（需要 history_manager 已注入）。首次 refresh 时调用。"""
        if self._grid_image is not None or self._history is None:
            return
        self._grid_image = _HistoryGrid(self._history, self, parent=self._tabs)
        self._grid_video = _HistoryGrid(self._history, self, parent=self._tabs)
        self._tabs.addTab(self._grid_image, "图片")
        self._tabs.addTab(self._grid_video, "视频")

    def _apply_palette(self) -> None:
        if self._count_label is not None:
            self._count_label.setStyleSheet(f"color: {self._palette.fg_sub};")
        if self._lbl_page is not None:
            self._lbl_page.setStyleSheet(f"color: {self._palette.fg_sub};")
        if self._empty_hint is not None:
            self._empty_hint.setStyleSheet(f"color: {self._palette.fg_sub}; padding: 60px;")

    # ---- 数据刷新 ----
    def _current_type(self) -> str | None:
        """当前 Tab 对应的类型。0=图片, 1=视频。"""
        if self._tabs is None:
            return None
        return "image" if self._tabs.currentIndex() == 0 else "video"

    def _current_since(self) -> float | None:
        """时间范围下拉对应的时间戳下限。None=不限。"""
        if self._cmb_time is None:
            return None
        import time as _time
        days = {"全部": None, "今日": 0, "近三天": 3, "近七天": 7}
        d = days.get(self._cmb_time.currentText(), None)
        if d is None:
            return None
        if d == 0:  # 今日：取今天 0 点
            from datetime import datetime
            now = datetime.now()
            today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return today0.timestamp()
        return _time.time() - d * 86400

    def _apply_filter_and_page(self) -> None:
        """按当前筛选条件 + 当前 Tab 查询，取当前页切片加载到对应 grid。

        refresh() 的核心：不再全量加载，而是 query + 分页切片。
        """
        if self._history is None or self._tabs is None:
            return
        self._ensure_grids()
        rtype = self._current_type()
        matched = self._history.query(
            type=rtype,
            alarm_only=self._chk_alarm.isChecked(),
            since=self._current_since(),
            keyword=self._edt_search.text(),
        )
        self._filtered_total = len(matched)
        # 分页切片
        total_pages = max(1, (self._filtered_total + PAGE_SIZE - 1) // PAGE_SIZE)
        if self._page > total_pages:
            self._page = total_pages
        if self._page < 1:
            self._page = 1
        offset = (self._page - 1) * PAGE_SIZE
        page_records = matched[offset:offset + PAGE_SIZE]
        # 加载到对应 grid
        if rtype == "image":
            self._grid_image.load(page_records)
            self._grid_video.load([])
        else:
            self._grid_video.load(page_records)
            self._grid_image.load([])
        # 计数 + 页码
        self._count_label.setText(
            f"共 {self._filtered_total} 条（筛选后）"
        )
        self._lbl_page.setText(f"第 {self._page} / {total_pages} 页")
        self._btn_prev.setEnabled(self._page > 1)
        self._btn_next.setEnabled(self._page < total_pages)
        # Tab 标题带数量
        self._tabs.setTabText(0, f"图片")
        self._tabs.setTabText(1, f"视频")
        # 空状态：完全无记录（未筛选也无）时显示提示
        has_any = len(self._history.all_records()) > 0
        self._empty_hint.setVisible(not has_any)
        self._tabs.setVisible(has_any)

    def refresh(self) -> None:
        """从 HistoryManager 重新加载（保留当前筛选/页码）。"""
        self._apply_filter_and_page()

    def _on_filter_changed(self) -> None:
        """筛选条件变化：重置到第 1 页并重新加载。"""
        self._page = 1
        self._apply_filter_and_page()

    def _on_tab_changed(self) -> None:
        """切换 Tab：重置到第 1 页并重新加载。"""
        self._page = 1
        self._apply_filter_and_page()

    def _on_prev_page(self) -> None:
        if self._page > 1:
            self._page -= 1
            self._apply_filter_and_page()

    def _on_next_page(self) -> None:
        total_pages = max(1, (self._filtered_total + PAGE_SIZE - 1) // PAGE_SIZE)
        if self._page < total_pages:
            self._page += 1
            self._apply_filter_and_page()

    def load_thumb(self, record: "HistoryRecord") -> QPixmap | None:
        """加载缩略图为 QPixmap。供 _HistoryGrid 调用。"""
        if self._history is None:
            return None
        path = self._history.thumb_path(record)
        if not os.path.isfile(path):
            return None
        try:
            pm = QPixmap(path)
            if pm.isNull():
                return None
            return pm.scaled(THUMB_W, THUMB_H - 30, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except Exception:
            return None

    # ---- 打开记录（在页面右侧内嵌预览，不再弹独立窗口）----
    def open_record(self, record: "HistoryRecord") -> None:
        if self._history is None:
            return
        file_path = self._history.file_path(record)
        if record.type == "video":
            self._play_video(file_path, record.label())
        else:
            self._show_image(file_path, record.label())

    def _play_video(self, path: str, title: str) -> None:
        if not os.path.isfile(path):
            QMessageBox.warning(self, "文件缺失", f"视频文件不存在：\n{path}")
            return
        self._preview.play_video(path, title=title)

    def _show_image(self, path: str, title: str) -> None:
        if not os.path.isfile(path):
            QMessageBox.warning(self, "文件缺失", f"图片文件不存在：\n{path}")
            return
        self._preview.show_image(path, title=title)

    def _on_clear_all(self) -> None:
        if self._history is None:
            return
        reply = QMessageBox.question(
            self, "清空全部", "确定清空所有历史记录？所有图片和视频文件都会被删除，不可恢复。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._history.clear_all()
            self.refresh()
            self._preview.show_empty()
