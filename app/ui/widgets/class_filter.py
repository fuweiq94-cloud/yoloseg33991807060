"""类别筛选控件：勾选要检测的 COCO 类别。"""
from __future__ import annotations

import json
import os
from typing import List

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QScrollArea, QCheckBox, QPushButton,
    QLineEdit, QHBoxLayout, QFrame,
)


def load_classes(config_path: str) -> dict:
    """加载 config/classes.json -> {str(id): {"en":..,"zh":..}}。"""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


class ClassFilter(QWidget):
    """类别筛选面板。发出 selected_classes(list[int])。"""

    selected_classes = pyqtSignal(list)

    def __init__(self, classes_meta: dict, default_selected: List[int] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._meta = classes_meta
        self._checks: dict[int, QCheckBox] = {}
        # default_selected=None 表示全选（用于"报警类别"等默认全启用场景）。
        # 显式空列表 [] 退化为 [0]，与原行为一致（仅检测人）。
        if default_selected is None:
            default_selected = [int(k) for k in classes_meta.keys()]
        self._build(default_selected)

    def _build(self, default_selected: List[int]) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # 搜索框
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索类别 / search")
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        # 操作按钮
        btn_row = QHBoxLayout()
        btn_all = QPushButton("全选")
        btn_none = QPushButton("全不选")
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none.clicked.connect(lambda: self._set_all(False))
        btn_all.setProperty("role", "flat")
        btn_none.setProperty("role", "flat")
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        layout.addLayout(btn_row)

        # 可滚动勾选区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QFrame()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(2, 2, 2, 2)
        vbox.setSpacing(2)
        for cid_str, meta in sorted(self._meta.items(), key=lambda kv: int(kv[0])):
            cid = int(cid_str)
            text = f"{cid} {meta.get('zh', meta.get('en', str(cid)))} ({meta.get('en', '')})"
            cb = QCheckBox(text)
            cb.setChecked(cid in default_selected)
            cb.stateChanged.connect(self._on_changed)
            self._checks[cid] = cb
            vbox.addWidget(cb)
        vbox.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll)

    def _on_search(self, text: str) -> None:
        text = text.strip().lower()
        for cid, cb in self._checks.items():
            meta = self._meta.get(str(cid), {})
            label = f"{cid} {meta.get('zh','')} {meta.get('en','')}".lower()
            cb.setVisible(not text or text in label)
            cb.parentWidget().layout().update() if cb.parentWidget() else None

    def _set_all(self, checked: bool) -> None:
        for cb in self._checks.values():
            if cb.isVisible():
                cb.setChecked(checked)

    def _on_changed(self) -> None:
        selected = [cid for cid, cb in self._checks.items() if cb.isChecked()]
        self.selected_classes.emit(selected)

    def get_selected(self) -> List[int]:
        return [cid for cid, cb in self._checks.items() if cb.isChecked()]

    def set_selected(self, ids: List[int]) -> None:
        for cid, cb in self._checks.items():
            cb.setChecked(cid in ids)
