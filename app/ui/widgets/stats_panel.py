"""统计图表面板：柱状图 / 折线图 / 饼图 / 报警趋势，用 pyqtgraph 实现。

四个图表共享一个数据源（StatsCollector 的查询接口），通过 Tab 切换或分页切换。
为避免阻塞 UI，图表刷新由主线程收到 stats_ready 信号后驱动，节流到 ~1Hz。
"""
from __future__ import annotations

import math
from typing import List

import numpy as np
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QButtonGroup, QLabel,
    QSizePolicy,
)

import pyqtgraph as pg

from app.ui.theme import get_palette
from app.ui.widgets.svg_icon import load_svg_pixmap


class StatsPanel(QWidget):
    """统计面板。四种图表按按钮切换；数据通过 update_stats() 推入。

    数据持有：本控件缓存最近一次查询结果，切换图表时即时重绘，无需重新查询。
    """

    # 当前图表类型
    CHART_BAR = "bar"          # 类别柱状图
    CHART_LINE = "line"        # 目标数随时间折线
    CHART_PIE = "pie"          # 类别占比饼图
    CHART_ALARM = "alarm"      # 报警趋势折线

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = get_palette("dark")
        self._chart = self.CHART_BAR

        # 缓存数据
        self._class_counts: dict[str, int] = {}
        self._class_ratio: dict[str, float] = {}
        self._time_labels: List[str] = []
        self._time_values: List[int] = []
        self._alarm_labels: List[str] = []
        self._alarm_values: List[int] = []
        self._alarm_total: int = 0

        # 节流：stats_ready 高频触发，刷新控制在 ~1Hz
        self._dirty = False
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

        self._build()
        self._apply_pg_style()

    def set_palette(self, palette) -> None:
        self._palette = palette
        self._apply_pg_style()
        self._redraw()

    # ---- 构建界面 ----
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        # 切换按钮行
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self._btns: dict[str, QPushButton] = {}
        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)
        for key, text in (
            (self.CHART_BAR, "类别柱状"),
            (self.CHART_LINE, "目标趋势"),
            (self.CHART_PIE, "类别占比"),
            (self.CHART_ALARM, "报警趋势"),
        ):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setProperty("role", "flat")
            b.clicked.connect(lambda _checked=False, k=key: self._switch_chart(k))
            self._btn_group.addButton(b)
            self._btns[key] = b
            bar.addWidget(b)
        self._btns[self.CHART_BAR].setChecked(True)

        self._summary = QLabel("目标 0  报警 0")
        self._summary.setProperty("role", "sub")
        bar.addStretch(1)
        bar.addWidget(self._summary)
        layout.addLayout(bar)

        # 图表容器
        self._plot_container = QWidget()
        self._plot_layout = QVBoxLayout(self._plot_container)
        self._plot_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plot_container, 1)

        self._current_widget: QWidget | None = None
        self._redraw()

    def _apply_pg_style(self) -> None:
        p = self._palette
        pg.setConfigOption("background", p.bg_panel)
        pg.setConfigOption("foreground", p.fg_main)
        pg.setConfigOption("antialias", True)

    def _clear_plot(self) -> None:
        if self._current_widget is not None:
            self._plot_layout.removeWidget(self._current_widget)
            self._current_widget.setParent(None)
            self._current_widget.deleteLater()
            self._current_widget = None

    # ---- 切换图表 ----
    def _switch_chart(self, key: str) -> None:
        self._chart = key
        self._redraw()

    # ---- 公共接口：推数据 ----
    def update_class_counts(self, counts: dict[str, int]) -> None:
        self._class_counts = dict(counts)
        self._dirty = True

    def update_class_ratio(self, ratio: dict[str, float]) -> None:
        self._class_ratio = dict(ratio)
        self._dirty = True

    def update_time_series(self, labels: List[str], values: List[int]) -> None:
        self._time_labels = list(labels)
        self._time_values = list(values)
        self._dirty = True

    def update_alarm_trend(self, labels: List[str], values: List[int]) -> None:
        self._alarm_labels = list(labels)
        self._alarm_values = list(values)
        self._dirty = True

    def update_alarm_total(self, total: int) -> None:
        self._alarm_total = int(total)
        self._dirty = True

    def mark_dirty(self) -> None:
        self._dirty = True

    # ---- 导出支持：供 StatsPage 取当前图表 widget/数据 ----
    def current_chart_widget(self) -> QWidget | None:
        """当前图表的 widget（PlotWidget/GraphicsLayoutWidget），供 grab() 截图导出 PNG。"""
        return self._current_widget

    def current_chart_data(self) -> tuple[str, list[tuple[str, float]]] | None:
        """当前图表的数据快照，返回 (类型名, [(标签, 值), ...])。无数据时 None。

        - BAR:   ("类别柱状", [(类名, 数量), ...])
        - PIE:   ("类别占比", [(类名, 占比0-1), ...])
        - LINE:  ("目标趋势", [(时间标签, 目标数), ...])
        - ALARM: ("报警趋势", [(时间标签, 报警数), ...])
        返回的是缓存的拷贝，所见即所得（与屏幕显示一致）。
        """
        if self._chart == self.CHART_BAR:
            if not self._class_counts:
                return None
            return ("类别柱状", [(k, float(v)) for k, v in self._class_counts.items()])
        if self._chart == self.CHART_PIE:
            if not self._class_ratio:
                return None
            return ("类别占比", [(k, float(v)) for k, v in self._class_ratio.items()])
        if self._chart == self.CHART_LINE:
            if not self._time_labels:
                return None
            return ("目标趋势", list(zip(self._time_labels, [float(v) for v in self._time_values])))
        if self._chart == self.CHART_ALARM:
            if not self._alarm_labels:
                return None
            return ("报警趋势", list(zip(self._alarm_labels, [float(v) for v in self._alarm_values])))
        return None

    def _flush(self) -> None:
        if self._dirty:
            self._dirty = False
            self._redraw()
            obj_total = sum(self._class_counts.values())
            self._summary.setText(f"目标 {obj_total}  报警 {self._alarm_total}")

    # ---- 绘制 ----
    def _redraw(self) -> None:
        self._clear_plot()
        if self._chart == self.CHART_BAR:
            self._draw_bar()
        elif self._chart == self.CHART_LINE:
            self._draw_line()
        elif self._chart == self.CHART_PIE:
            self._draw_pie()
        elif self._chart == self.CHART_ALARM:
            self._draw_alarm()

    def _new_plot(self, title: str) -> pg.PlotWidget:
        p = self._palette
        plot = pg.PlotWidget()
        plot.setBackground(p.bg_panel)
        plot.showGrid(x=False, y=True, alpha=0.15)
        plot.getAxis("left").setPen(p.fg_sub)
        plot.getAxis("bottom").setPen(p.fg_sub)
        plot.getAxis("left").setTextPen(p.fg_sub)
        plot.getAxis("bottom").setTextPen(p.fg_sub)
        plot.setLabel("left", title)
        plot.setMouseEnabled(False, False)
        plot.hideButtons()
        return plot

    def _draw_bar(self) -> None:
        items = sorted(self._class_counts.items(), key=lambda kv: kv[1], reverse=True)
        if not items:
            self._show_empty("暂无类别数据")
            return
        labels = [k for k, _ in items]
        values = [v for _, v in items]

        plot = self._new_plot("数量")
        bar = pg.BarGraphItem(
            x=list(range(len(values))),
            height=values,
            width=0.6,
            brush=self._palette.primary,
            pen=pg.mkPen(self._palette.primary_lo, width=1),
        )
        plot.addItem(bar)
        ticks = [[(i, labels[i]) for i in range(len(labels))]]
        plot.getAxis("bottom").setTicks(ticks)
        plot.setXRange(-0.5, len(values) - 0.5, padding=0.05)
        self._install(plot)

    def _draw_line(self) -> None:
        if not self._time_values:
            self._show_empty("暂无目标趋势数据")
            return
        plot = self._new_plot("目标数")
        x = list(range(len(self._time_values)))
        plot.plot(
            x, self._time_values,
            pen=pg.mkPen(self._palette.primary, width=2),
            symbol="o", symbolSize=5,
            symbolBrush=self._palette.primary, symbolPen=None,
        )
        # 横轴：最多显示 8 个时间标签，避免拥挤
        n = len(self._time_labels)
        step = max(1, math.ceil(n / 8))
        ticks = [[(i, self._time_labels[i]) for i in range(0, n, step)]]
        plot.getAxis("bottom").setTicks(ticks)
        self._install(plot)

    def _draw_pie(self) -> None:
        items = sorted(self._class_ratio.items(), key=lambda kv: kv[1], reverse=True)
        if not items:
            self._show_empty("暂无类别占比数据")
            return
        view = pg.GraphicsLayoutWidget()
        view.setBackground(self._palette.bg_panel)
        # 注意：GraphicsLayoutWidget.addItem 要求子项有 geometryChanged 信号，
        # 而 _PieItem 是裸 GraphicsObject 不具备；必须先 addPlot() 得到 PlotItem，
        # 再 PlotItem.addItem(pie) —— 这一路 ViewBox.addItem 能正确处理任意 GraphicsObject。
        plt = view.addPlot()
        plt.hideAxis("left")
        plt.hideAxis("bottom")
        plt.setMouseEnabled(x=False, y=False)
        plt.hideButtons()
        plt.showGrid(x=False, y=False)
        # pg 无原生饼图，用 GraphicsObject 自绘扇形
        pie = _PieItem(items, self._palette)
        plt.addItem(pie)
        plt.autoRange()
        # 图例
        legend = QLabel(self._pie_legend(items))
        legend.setProperty("role", "sub")
        legend.setWordWrap(True)
        legend.setAlignment(Qt.AlignCenter)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(view, 1)
        wl.addWidget(legend)
        self._install(wrap)

    def _draw_alarm(self) -> None:
        if not self._alarm_values:
            self._show_empty("暂无报警趋势数据")
            return
        plot = self._new_plot("报警数")
        x = list(range(len(self._alarm_values)))
        plot.plot(
            x, self._alarm_values,
            pen=pg.mkPen(self._palette.alarm, width=2),
            fillLevel=0,
            brush=pg.mkBrush(self._palette.alarm),
            symbol="o", symbolSize=5,
            symbolBrush=self._palette.alarm, symbolPen=None,
        )
        n = len(self._alarm_labels)
        step = max(1, math.ceil(n / 8))
        ticks = [[(i, self._alarm_labels[i]) for i in range(0, n, step)]]
        plot.getAxis("bottom").setTicks(ticks)
        self._install(plot)

    def _show_empty(self, text: str) -> None:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setAlignment(Qt.AlignCenter)
        v.setSpacing(10)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(load_svg_pixmap("empty_chart", self._palette.fg_sub, 56))
        icon_lbl.setAlignment(Qt.AlignCenter)
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setProperty("role", "sub")
        v.addWidget(icon_lbl)
        v.addWidget(lbl)
        self._install(wrap)

    def _install(self, widget: QWidget) -> None:
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._plot_layout.addWidget(widget)
        self._current_widget = widget

    @staticmethod
    def _pie_legend(items) -> str:
        return "  ".join(f"{name} {ratio * 100:.0f}%" for name, ratio in items[:8])


class _PieItem(pg.GraphicsObject):
    """自绘饼图扇形。pg 无内置饼图，按角度切片。"""

    def __init__(self, items: list[tuple[str, float]], palette) -> None:
        super().__init__()
        self._items = items
        self._palette = palette
        # 离散调色板（钢蓝渐变 + 中性色），按占比顺序分配
        self._colors = [
            palette.primary, palette.primary_hi, palette.success,
            palette.warn, palette.alarm, "#7E57C2", "#00ACC1", "#78909C",
        ]
        self.picture = pg.Qt.QtGui.QPicture()
        self._draw()

    def _draw(self) -> None:
        painter = pg.Qt.QtGui.QPainter(self.picture)
        painter.setRenderHint(pg.Qt.QtGui.QPainter.Antialiasing)

        total = sum(r for _, r in self._items)
        if total <= 0:
            painter.end()
            return

        import math as _m
        # 在 100x100 坐标系中画
        cx, cy, r = 0.0, 0.0, 40.0
        start = _m.pi / 2  # 从 12 点钟方向开始
        for idx, (name, ratio) in enumerate(self._items):
            span = ratio / total * 2 * _m.pi
            color = self._colors[idx % len(self._colors)]
            path = pg.Qt.QtGui.QPainterPath()
            path.moveTo(cx, cy)
            # Qt 坐标 y 向下；用参数方程
            x1 = cx + r * _m.cos(start)
            y1 = cy - r * _m.sin(start)
            path.lineTo(x1, y1)
            steps = max(8, int(span / 0.1))
            for s in range(1, steps + 1):
                a = start + span * s / steps
                path.lineTo(cx + r * _m.cos(a), cy - r * _m.sin(a))
            path.closeSubpath()
            painter.setPen(pg.mkPen(self._palette.bg_panel, width=1))
            painter.setBrush(pg.mkBrush(color))
            painter.drawPath(path)
            start += span
        painter.end()

    def paint(self, painter, option, widget=None):
        painter.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return pg.Qt.QtCore.QRectF(self.picture.boundingRect())
