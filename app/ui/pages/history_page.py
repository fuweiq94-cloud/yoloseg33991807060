"""历史记录页：以缩略图网格展示图片/视频识别历史，点击查看或播放。

数据由 HistoryManager 提供（MainWindow 注入）。本页只负责展示与交互：
- 双击/回车：图片放大查看 / 视频播放
- 右键菜单：删除单条
- 顶部按钮：刷新 / 清空全部
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon, QPixmap, QImage
from PyQt5.QtWidgets import (
    QListWidget, QListWidgetItem, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QWidget, QMenu, QAction, QAbstractItemView, QMessageBox,
)

from app.ui.pages.base_page import BasePage
from app.ui.theme import Palette
from app.ui.widgets.svg_icon import load_svg_icon
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.core.history import HistoryManager, HistoryRecord

logger = get_logger()

THUMB_W, THUMB_H = 200, 130  # 网格缩略图尺寸（含文字区）


class HistoryPage(BasePage):
    """历史记录页。"""

    title = "历史记录"
    icon_name = "history"

    def __init__(self, parent: QWidget | None = None) -> None:
        # history_manager 由 MainWindow 调用 set_history_manager 注入
        self._history: "HistoryManager | None" = None
        self._list: QListWidget | None = None
        self._empty_hint: QLabel | None = None
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

        # 空状态提示
        self._empty_hint = QLabel("暂无历史记录\n\n进行图片/视频识别后点击「保存」按钮，结果会出现在这里")
        self._empty_hint.setAlignment(Qt.AlignCenter)
        self._empty_hint.setProperty("role", "sub")
        self._empty_hint.setStyleSheet(f"color: {self._palette.fg_sub}; padding: 60px;")
        layout.addWidget(self._empty_hint)

        # 缩略图网格
        self._list = QListWidget()
        self._list.setViewMode(QListWidget.IconMode)
        self._list.setIconSize(QSize(THUMB_W, THUMB_H))
        self._list.setResizeMode(QListWidget.Adjust)
        self._list.setMovement(QListWidget.Static)
        self._list.setSpacing(8)
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.setUniformItemSizes(True)
        self._list.itemDoubleClicked.connect(self._on_item_activated)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._list, 1)

        self._root_layout.addLayout(layout, 1)

    def _apply_palette(self) -> None:
        if self._count_label is not None:
            self._count_label.setStyleSheet(f"color: {self._palette.fg_sub};")
        if self._empty_hint is not None:
            self._empty_hint.setStyleSheet(f"color: {self._palette.fg_sub}; padding: 60px;")

    # ---- 数据刷新 ----
    def refresh(self) -> None:
        """从 HistoryManager 重新加载并展示所有记录。"""
        if self._history is None or self._list is None:
            return
        self._list.clear()
        records = self._history.all_records()
        self._count_label.setText(f"{len(records)} 条记录")
        self._empty_hint.setVisible(len(records) == 0)
        self._list.setVisible(len(records) > 0)
        for rec in records:
            item = QListWidgetItem()
            # 标题：来源名 + 换行 + 时间 + 统计
            item.setText(f"{rec.label()}\n{rec.time_str()}\n{rec.sublabel()}")
            item.setTextAlignment(Qt.AlignCenter)
            # 缩略图
            pm = self._load_thumb(rec)
            if pm is not None:
                item.setIcon(QIcon(pm))
            # 类型角标：视频加播放标记
            if rec.type == "video":
                font = item.font()
                item.setToolTip(f"视频 · {rec.duration:.1f}s\n{rec.time_str()}")
            else:
                item.setToolTip(f"图片\n{rec.time_str()}")
            item.setData(Qt.UserRole, rec.id)
            item.setData(Qt.UserRole + 1, rec.type)
            self._list.addItem(item)

    def _load_thumb(self, record: "HistoryRecord") -> QPixmap | None:
        """加载缩略图为 QPixmap（带视频/图片角标叠加）。失败返回 None。"""
        path = self._history.thumb_path(record)
        if not os.path.isfile(path):
            return None
        try:
            pm = QPixmap(path)
            if pm.isNull():
                return None
            # 缩放到图标尺寸
            return pm.scaled(THUMB_W, THUMB_H - 30, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except Exception:
            return None

    # ---- 交互 ----
    def _on_item_activated(self, item: QListWidgetItem) -> None:
        record_id = item.data(Qt.UserRole)
        if self._history is None:
            return
        record = self._history.get(record_id)
        if record is None:
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
        from app.ui.widgets.video_player_dialog import VideoPlayerDialog
        dlg = VideoPlayerDialog(path, title=f"回放 - {title}", parent=self)
        dlg.exec_()

    def _show_image(self, path: str, title: str) -> None:
        if not os.path.isfile(path):
            QMessageBox.warning(self, "文件缺失", f"图片文件不存在：\n{path}")
            return
        # 复用 anomaly_list 的放大对话框模式：简单 QDialog + QLabel
        from PyQt5.QtWidgets import QDialog
        dlg = QDialog(self)
        dlg.setWindowTitle(f"查看 - {title}")
        dlg.setModal(True)
        dlg.resize(900, 700)
        v = QVBoxLayout(dlg)
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignCenter)
        pm = QPixmap(path)
        if not pm.isNull():
            lbl.setPixmap(pm.scaled(900, 700, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        v.addWidget(lbl, 1)
        btn = QPushButton("关闭")
        btn.clicked.connect(dlg.accept)
        v.addWidget(btn)
        dlg.exec_()

    def _on_context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if item is None or self._history is None:
            return
        menu = QMenu(self)
        act_open = QAction("打开", self)
        act_del = QAction("删除", self)
        menu.addAction(act_open)
        menu.addAction(act_del)
        action = menu.exec_(self._list.mapToGlobal(pos))
        if action is act_open:
            self._on_item_activated(item)
        elif action is act_del:
            record_id = item.data(Qt.UserRole)
            reply = QMessageBox.question(
                self, "删除", "确定删除这条历史记录？文件也会被删除。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._history.delete(record_id)
                self.refresh()

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
