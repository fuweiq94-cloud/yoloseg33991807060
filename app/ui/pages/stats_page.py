"""统计页：四图表切换 + 异常帧列表，垂直布局。

薄封装层：包裹现有 StatsPanel（图表）与 AnomalyList（异常帧缩略图）。
数据由 MainWindow 从 StatsCollector 查询后推入。
"""
from __future__ import annotations

from typing import List

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QSplitter

from app.ui.pages.base_page import BasePage
from app.ui.widgets.stats_panel import StatsPanel
from app.ui.widgets.anomaly_list import AnomalyList, AnomalyItem


class StatsPage(BasePage):
    title = "统计与异常帧"

    def _build_content(self) -> None:
        split = QSplitter(Qt.Vertical)

        self.stats_panel = StatsPanel()
        self.stats_panel.set_palette(self.palette)
        split.addWidget(self.stats_panel)

        self.anomaly_list = AnomalyList(max_items=100)
        self.anomaly_list.set_palette(self.palette)
        split.addWidget(self.anomaly_list)

        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([300, 220])
        self._root_layout.addWidget(split, 1)

    def _apply_palette(self) -> None:
        self.stats_panel.set_palette(self.palette)
        self.anomaly_list.set_palette(self.palette)

    # ---- MainWindow 驱动 ----
    def update_class_counts(self, counts: dict) -> None:
        self.stats_panel.update_class_counts(counts)

    def update_class_ratio(self, ratio: dict) -> None:
        self.stats_panel.update_class_ratio(ratio)

    def update_time_series(self, labels: List[str], values: List[int]) -> None:
        self.stats_panel.update_time_series(labels, values)

    def update_alarm_trend(self, labels: List[str], values: List[int]) -> None:
        self.stats_panel.update_alarm_trend(labels, values)

    def update_alarm_total(self, total: int) -> None:
        self.stats_panel.update_alarm_total(total)

    def mark_stats_dirty(self) -> None:
        self.stats_panel.mark_dirty()

    def append_anomaly(self, item: AnomalyItem) -> None:
        self.anomaly_list.append_item(item)

    def clear_anomaly(self) -> None:
        self.anomaly_list.clear()
