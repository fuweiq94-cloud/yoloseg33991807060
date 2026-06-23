"""统计页：四图表切换 + 异常帧列表，垂直布局。

薄封装层：包裹现有 StatsPanel（图表）与 AnomalyList（异常帧缩略图）。
数据由 MainWindow 从 StatsCollector 查询后推入。
"""
from __future__ import annotations

from typing import List

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QSplitter, QPushButton, QFileDialog, QMessageBox

from app.ui.pages.base_page import BasePage
from app.ui.widgets.stats_panel import StatsPanel
from app.ui.widgets.anomaly_list import AnomalyList, AnomalyItem
from app.ui.widgets.svg_icon import load_svg_icon


class StatsPage(BasePage):
    title = "统计与异常帧"
    icon_name = "stats"

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

        # 标题栏右侧加导出按钮（BasePage._header 在 addStretch 后留有空间）
        self._btn_export_png = QPushButton("导出 PNG")
        self._btn_export_png.setProperty("role", "flat")
        self._btn_export_png.setIcon(load_svg_icon("export", self.palette.fg_main, 16))
        self._btn_export_png.clicked.connect(self._export_png)
        self._btn_export_csv = QPushButton("导出 CSV")
        self._btn_export_csv.setProperty("role", "flat")
        self._btn_export_csv.setIcon(load_svg_icon("export", self.palette.fg_main, 16))
        self._btn_export_csv.clicked.connect(self._export_csv)
        self._header.addWidget(self._btn_export_png)
        self._header.addWidget(self._btn_export_csv)

    def _apply_palette(self) -> None:
        self.stats_panel.set_palette(self.palette)
        self.anomaly_list.set_palette(self.palette)

    # ---- 导出 ----
    def _export_png(self) -> None:
        """导出当前图表为 PNG（widget.grab 截图，四种图表统一处理，含背景/图例）。"""
        widget = self.stats_panel.current_chart_widget()
        if widget is None:
            QMessageBox.information(self, "无数据", "当前图表无内容可导出。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出图表 PNG", "chart.png", "PNG 图片 (*.png)")
        if not path:
            return
        pm = widget.grab()
        if pm.save(path):
            QMessageBox.information(self, "导出成功", f"图表已导出到：\n{path}")
        else:
            QMessageBox.warning(self, "导出失败", "写入 PNG 失败，请检查路径权限。")

    def _export_csv(self) -> None:
        """导出当前图表数据为 CSV（两列：标签,值），数据取自 stats_panel 缓存（所见即所得）。"""
        import csv
        data = self.stats_panel.current_chart_data()
        if data is None:
            QMessageBox.information(self, "无数据", "当前图表无数据可导出。")
            return
        chart_name, rows = data
        path, _ = QFileDialog.getSaveFileName(self, "导出图表数据 CSV", "chart.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["项目", "值"])
                for label, value in rows:
                    # 占比类保留 4 位小数，计数类整数
                    if chart_name == "类别占比":
                        writer.writerow([label, f"{value:.4f}"])
                    else:
                        writer.writerow([label, int(value)])
            QMessageBox.information(self, "导出成功", f"{chart_name} 数据已导出到：\n{path}")
        except OSError:
            QMessageBox.warning(self, "导出失败", "写入 CSV 失败，请检查路径权限。")

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
