"""数据统计列表插件：用表格/列表展示检测统计（不做任何图表）。

展示内容：
  - 顶部概览：累计不同目标数、累计报警数、统计开始时间、最近刷新时刻
  - 主表格：每一行一个类别，列为「类别 / 数量 / 占比 / 进度条」
  - 底部：报警时间序列（按分钟分箱的数值列表，纯文字）

数据来源：ctx.stats（StatsCollector）。通过订阅 on_frame 实时刷新，
节流到每 500ms 最多刷一次，避免每帧重建表格拖性能。
"""
from __future__ import annotations

from time import time as _now

from PyQt5.QtWidgets import QTableWidgetItem
from PyQt5.QtGui import QColor

from app.plugins.base import Plugin, PluginContext


class StatsListPlugin(Plugin):
    name = "stats_list"
    title = "数据统计"
    icon = "icon.svg"        # 插件目录下自带图标
    nav_after = ""           # 追加到导航末尾

    # 刷新节流间隔（秒）：on_frame 每帧都触发，但表格重建昂贵，限频。
    _REFRESH_THROTTLE = 0.5

    def create_widget(self):
        from PyQt5.QtWidgets import (
            QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
        )
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QFont, QColor

        self._last_refresh_ts: float = 0.0
        self._start_ts: float | None = None   # 首次有数据时记录

        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        # ---- 标题 ----
        title = QLabel("📊 数据统计")
        f = QFont(); f.setPointSize(16); f.setBold(True)
        title.setFont(f)
        v.addWidget(title)
        hint = QLabel("实时统计各检测类别的累计数量与占比（数据来自 StatsCollector，纯列表展示）")
        hint.setProperty("role", "sub")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ---- 概览卡片 ----
        self._card = QFrame()
        cl = QHBoxLayout(self._card)
        cl.setContentsMargins(12, 12, 12, 12)
        cl.setSpacing(16)
        self._lbl_total = self._overview_cell(cl, "累计目标", "0")
        self._lbl_alarm = self._overview_cell(cl, "累计报警", "0")
        self._lbl_start = self._overview_cell(cl, "统计开始", "—")
        self._lbl_updated = self._overview_cell(cl, "最近刷新", "—")
        v.addWidget(self._card)

        # ---- 类别统计表格 ----
        tbl_title = QLabel("各类别统计")
        tf = QFont(); tf.setPointSize(12); tf.setBold(True)
        tbl_title.setFont(tf)
        v.addWidget(tbl_title)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["类别", "数量", "占比", "占比"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setAlternatingRowColors(True)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)   # 类别列拉伸
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)   # 进度条列拉伸
        v.addWidget(self._table, 1)

        # ---- 报警时间序列（纯文字列表）----
        alarm_title = QLabel("报警时间序列（按分钟分箱）")
        alarm_title.setFont(tf)
        v.addWidget(alarm_title)
        self._lbl_alarm_series = QLabel("（暂无数据）")
        self._lbl_alarm_series.setWordWrap(True)
        self._lbl_alarm_series.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 12px;"
            " background: #f5f5f5; padding: 8px; border-radius: 4px; color: #444;"
        )
        self._lbl_alarm_series.setMinimumHeight(60)
        v.addWidget(self._lbl_alarm_series)

        # ---- 操作行 ----
        row = QHBoxLayout()
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh_now)
        btn_reset = QPushButton("重置统计")
        btn_reset.setStyleSheet("color: #c0392b;")
        btn_reset.clicked.connect(self._on_reset)
        row.addWidget(btn_refresh)
        row.addWidget(btn_reset)
        row.addStretch(1)
        v.addLayout(row)

        self._widget = w
        self._apply_card_style()
        # 首次填充（可能已有数据）
        self.refresh_now()
        return w

    def _overview_cell(self, layout, label: str, value: str):
        """概览卡片里的一个单元格：上行小标题，下行大数值。返回数值 Label 供后续更新。"""
        from PyQt5.QtWidgets import QVBoxLayout, QLabel
        from PyQt5.QtGui import QFont
        box = QVBoxLayout()
        box.setSpacing(2)
        l1 = QLabel(label)
        l1.setStyleSheet("color: #888; font-size: 11px;")
        l2 = QLabel(value)
        f = QFont(); f.setPointSize(15); f.setBold(True)
        l2.setFont(f)
        box.addWidget(l1)
        box.addWidget(l2)
        layout.addLayout(box, 1)
        return l2

    # ---- 数据刷新 ----
    def refresh_now(self) -> None:
        """立刻从 ctx.stats 读数据并重建表格。"""
        from datetime import datetime
        stats = self.ctx.stats
        if stats is None:
            return
        try:
            counts = stats.class_counts()       # {类名: 累计数}
            ratio = stats.class_ratio()         # {类名: 0-1}
            alarm_total = stats.alarm_total
            labels, values = stats.alarm_trend(60)  # 按分钟分箱
        except Exception:
            return

        # 概览
        total_objs = sum(counts.values())
        self._lbl_total.setText(str(total_objs))
        self._lbl_alarm.setText(str(alarm_total))
        if self._start_ts is None and total_objs > 0:
            self._start_ts = _now()
        if self._start_ts is not None:
            self._lbl_start.setText(datetime.fromtimestamp(self._start_ts).strftime("%H:%M:%S"))
        self._lbl_updated.setText(datetime.now().strftime("%H:%M:%S"))

        # 表格：按数量降序
        rows = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        self._table.setRowCount(len(rows))
        for r, (name, n) in enumerate(rows):
            pct = ratio.get(name, 0.0)
            self._table.setItem(r, 0, QTableWidgetItem(str(name)))
            self._table.setItem(r, 1, QTableWidgetItem(str(n)))
            self._table.setItem(r, 2, QTableWidgetItem(f"{pct*100:.1f}%"))
            # 占比用「█」字符画进度条（纯文字，不依赖 QPainter）
            filled = int(pct * 20)  # 20 格刻度
            bar = "█" * filled + "░" * (20 - filled)
            bar_item = QTableWidgetItem(bar)
            bar_item.setForeground(QColor(self._bar_color()))
            self._table.setItem(r, 3, bar_item)
        # 空数据提示
        if not rows:
            self._table.setRowCount(1)
            empty = QTableWidgetItem("（暂无统计，开始检测后此处自动填充）")
            empty.setForeground(QColor("#999"))
            self._table.setItem(0, 0, empty)
            self._table.setSpan(0, 0, 1, 4)

        # 报警序列
        if labels and values:
            parts = [f"{lbl}:{val}" for lbl, val in zip(labels, values)]
            self._lbl_alarm_series.setText("  ".join(parts))
        else:
            self._lbl_alarm_series.setText("（暂无报警）")

    def _bar_color(self) -> str:
        return self.ctx.palette.primary if self.ctx.palette else "#3b82f6"

    # ---- 事件订阅 ----
    def on_frame(self, annotated, violator_indices, centers) -> None:
        """每帧触发，节流刷新（避免每帧重建表格）。"""
        now = _now()
        if now - self._last_refresh_ts < self._REFRESH_THROTTLE:
            return
        self._last_refresh_ts = now
        self.refresh_now()

    def on_theme_changed(self, palette) -> None:
        self._apply_card_style()

    # ---- 操作 ----
    def _on_reset(self) -> None:
        """重置统计（调 ctx.stats.reset()）。"""
        stats = self.ctx.stats
        if stats is None:
            return
        try:
            stats.reset()
        except Exception:
            pass
        self._start_ts = None
        self.refresh_now()
        self.ctx.show_status("[数据统计] 已重置")

    def _apply_card_style(self, palette=None) -> None:
        p = palette or self.ctx.palette
        self._card.setStyleSheet(
            f"QFrame {{ background: {p.bg_panel}; border: 1px solid {p.border}; border-radius: 6px; }}"
        )


def create_plugin(ctx: PluginContext) -> StatsListPlugin:
    return StatsListPlugin(ctx)
