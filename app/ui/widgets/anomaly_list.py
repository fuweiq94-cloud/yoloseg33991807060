"""异常帧列表：报警帧自动入列，横向滚动缩略图，点击放大回看。

每个异常帧保存为一张缩略图（QPixmap）+ 元信息（时间/类别/置信度/ROI）。
为限制内存，保留最近 N 条（默认 100），超过则丢弃最旧的。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List

import numpy as np
from PyQt5.QtCore import Qt, QSize, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame,
    QSizePolicy, QDialog, QPushButton,
)

from app.ui.theme import get_palette
from app.utils.image_convert import ndarray_bgr_to_qpixmap

THUMB_W = 160
THUMB_H = 90


@dataclass
class AnomalyItem:
    """单条异常帧记录。frame 存 BGR ndarray（小图，用于缩略图与放大）。"""
    timestamp: float
    frame: np.ndarray
    cls_names: List[str]
    confs: List[float]
    roi_id: int
    pixmap: QPixmap = field(default=None, repr=False)

    def time_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")

    def label(self) -> str:
        names = ",".join(self.cls_names) if self.cls_names else "?"
        conf = max(self.confs) if self.confs else 0.0
        return f"{self.time_str()} {names} {conf:.2f} ROI{self.roi_id}"


class _ThumbCard(QFrame):
    """单张缩略图卡片。clicked 信号携带对应 AnomalyItem。"""

    clicked = pyqtSignal(object)  # AnomalyItem

    def __init__(self, item: AnomalyItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedSize(THUMB_W + 8, THUMB_H + 24)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        self._thumb = QLabel()
        self._thumb.setFixedSize(THUMB_W, THUMB_H)
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setScaledContents(True)
        if item.pixmap is not None:
            self._thumb.setPixmap(item.pixmap)
        layout.addWidget(self._thumb)

        self._caption = QLabel(item.label())
        self._caption.setProperty("role", "sub")
        self._caption.setWordWrap(False)
        layout.addWidget(self._caption)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.item)


class AnomalyList(QWidget):
    """异常帧横向列表。append_item() 追加；点击放大。"""

    def __init__(self, max_items: int = 100, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = get_palette("dark")
        self._max_items = max_items
        self._items: List[AnomalyItem] = []
        self._build()

    def set_palette(self, palette) -> None:
        self._palette = palette
        self._update_placeholder()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        header = QHBoxLayout()
        title = QLabel("异常帧列表")
        title.setProperty("role", "title")
        self._count = QLabel("0")
        self._count.setProperty("role", "sub")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._count)
        self._clear_btn = QPushButton("清空")
        self._clear_btn.setProperty("role", "flat")
        self._clear_btn.clicked.connect(self.clear)
        header.addWidget(self._clear_btn)
        layout.addLayout(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._container = QFrame()
        self._row = QHBoxLayout(self._container)
        self._row.setContentsMargins(2, 2, 2, 2)
        self._row.setSpacing(6)
        self._row.addStretch(1)
        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll, 1)

        self._placeholder = QLabel("尚无异常帧")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setProperty("role", "sub")
        self._row.insertWidget(0, self._placeholder)

    def _update_placeholder(self) -> None:
        self._placeholder.setVisible(len(self._items) == 0)

    # ---- 公共接口 ----
    def append_item(self, item: AnomalyItem) -> None:
        """追加一条异常帧（已含 pixmap 则直接用，否则现场生成缩略图）。"""
        if item.pixmap is None:
            item.pixmap = self._make_thumbnail(item.frame)
        # 超额：移除最旧的卡片
        if len(self._items) >= self._max_items:
            self._remove_oldest()

        self._items.append(item)
        card = _ThumbCard(item, self._container)
        card.clicked.connect(self._show_enlarged)
        # 插到 stretch 之前
        self._row.insertWidget(self._row.count() - 1, card)
        self._count.setText(str(len(self._items)))
        self._update_placeholder()

    def clear(self) -> None:
        # 移除所有卡片（保留末尾的 stretch）
        while self._row.count() > 1:
            child = self._row.takeAt(0)
            w = child.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._items.clear()
        self._count.setText("0")
        self._update_placeholder()

    def _remove_oldest(self) -> None:
        if not self._items:
            return
        self._items.pop(0)
        # 第 0 个是卡片（stretch 在末尾），删除之
        child = self._row.takeAt(0)
        w = child.widget() if child else None
        if w is not None:
            w.setParent(None)
            w.deleteLater()

    @staticmethod
    def _make_thumbnail(frame_bgr: np.ndarray) -> QPixmap:
        pm = ndarray_bgr_to_qpixmap(frame_bgr)
        if pm.isNull():
            return QPixmap()
        return pm.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def _show_enlarged(self, item: AnomalyItem) -> None:
        dlg = _EnlargedDialog(item, self._palette, self)
        dlg.exec_()


class _EnlargedDialog(QDialog):
    """点击缩略图后的放大回看对话框。"""

    def __init__(self, item: AnomalyItem, palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("异常帧回看")
        self.setMinimumSize(640, 520)
        p = palette

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        view = QLabel()
        view.setAlignment(Qt.AlignCenter)
        view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        view.setStyleSheet(f"background-color: {p.canvas};")
        pm = ndarray_bgr_to_qpixmap(item.frame)
        if not pm.isNull():
            view.setPixmap(pm.scaled(
                800, 560, Qt.KeepAspectRatio, Qt.SmoothTransformation
            ))
        layout.addWidget(view, 1)

        meta = QLabel(item.label())
        meta.setProperty("role", "data")
        layout.addWidget(meta)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)
