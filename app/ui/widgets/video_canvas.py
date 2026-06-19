"""视频画布 + ROI 多边形绘制交互。

显示标注帧（带缩放适配），叠加 ROI 多边形（主题色），并对进入 ROI 的框描红高亮。
支持两种模式：
  - VIEW:  仅显示
  - DRAW:  鼠标点击逐点画多边形，双击/回车闭合，右键/ESC 取消

ROI 坐标以"原始帧像素"存储，缩放显示时实时换算，保证缩放/拉伸后判定正确。
"""
from __future__ import annotations

import base64
from typing import List, Tuple

import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal, QPoint, QPointF, QSize, QBuffer
from PyQt5.QtGui import (
    QPixmap, QImage, QPainter, QPen, QBrush, QColor, QPolygonF, QFont,
    QMouseEvent, QKeyEvent, QPaintEvent, QResizeEvent,
)
from PyQt5.QtWidgets import QLabel, QWidget

from app.ui.theme import get_palette
from app.ui.widgets.svg_icon import load_svg_pixmap


class VideoCanvas(QLabel):
    """显示视频帧并支持 ROI 绘制的画布。"""

    # 信号：ROI 绘制完成，发出顶点列表（原始帧像素坐标）
    roi_created = pyqtSignal(list)   # list[(x,y)] in frame pixels

    MODE_VIEW = 0
    MODE_DRAW = 1

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoCanvas")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(480, 320)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._pixmap: QPixmap | None = None
        self._frame_size: Tuple[int, int] = (0, 0)  # (w, h) 原始帧
        self._display_size: Tuple[int, int] = (0, 0)  # (w, h) 显示尺寸

        # 已有 ROI（原始像素坐标）
        self._rois: List[List[Tuple[float, float]]] = []
        # 违反框中心点（原始像素坐标），用于高亮
        self._violator_centers: List[Tuple[float, float]] = []
        self._violator_boxes: List[Tuple[float, float, float, float]] = []

        # 绘制模式状态
        self._mode = self.MODE_VIEW
        self._current_points: List[QPointF] = []  # 显示坐标

        self._palette = get_palette("dark")
        self._show_placeholder()

    def set_palette(self, palette) -> None:
        self._palette = palette
        self.update()

    # ---- 占位提示 ----
    def _show_placeholder(self) -> None:
        """空状态：居中显示插画 + 提示文字（富文本 HTML 内嵌 base64 PNG）。"""
        pm = load_svg_pixmap("empty_box", self._palette.fg_sub, 72)
        buf = QBuffer()
        buf.open(QBuffer.ReadWrite)
        pm.save(buf, "PNG")
        b64 = base64.b64encode(bytes(buf.data())).decode("ascii")
        html = (
            f"<div style='text-align:center;'>"
            f"<img src='data:image/png;base64,{b64}'/>"
            f"<br><span style='color:{self._palette.fg_sub}; font-size:13px;'>"
            f"请选择数据源并开始检测</span></div>"
        )
        self.setText(html)
        self.setStyleSheet(f"background-color: {self._palette.canvas};")

    # ---- 公共接口 ----
    def update_frame(self, frame_bgr: np.ndarray) -> None:
        """更新显示帧（BGR ndarray）。"""
        if frame_bgr is None or frame_bgr.size == 0:
            return
        rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
        h, w, ch = rgb.shape
        self._frame_size = (w, h)
        img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
        self._pixmap = QPixmap.fromImage(img)
        self.setText("")
        self.update()

    def set_rois(self, rois: List[List[Tuple[float, float]]]) -> None:
        """设置已有 ROI 列表（原始帧像素坐标）。"""
        self._rois = [list(r) for r in rois]
        self.update()

    def set_violators(
        self,
        centers: np.ndarray | None,
        violator_indices: list[int] | None,
        boxes: np.ndarray | None = None,
    ) -> None:
        """设置进入 ROI 的目标信息（用于红框高亮）。"""
        self._violator_centers = []
        self._violator_boxes = []
        if centers is None or violator_indices is None:
            self.update()
            return
        centers = np.asarray(centers)
        for idx in violator_indices:
            if 0 <= idx < len(centers):
                cx, cy = float(centers[idx, 0]), float(centers[idx, 1])
                self._violator_centers.append((cx, cy))
                if boxes is not None and idx < len(boxes):
                    b = boxes[idx]
                    self._violator_boxes.append((float(b[0]), float(b[1]), float(b[2]), float(b[3])))
        self.update()

    # ---- ROI 绘制模式 ----
    def start_drawing(self) -> None:
        self._mode = self.MODE_DRAW
        self._current_points.clear()
        self.setCursor(Qt.CrossCursor)
        self.setFocus()
        self.update()

    def cancel_drawing(self) -> None:
        self._mode = self.MODE_VIEW
        self._current_points.clear()
        self.setCursor(Qt.ArrowCursor)
        self.update()

    @property
    def is_drawing(self) -> bool:
        return self._mode == self.MODE_DRAW

    # ---- 坐标换算：显示坐标 <-> 原始帧像素 ----
    def _recalc_display_size(self) -> None:
        if self._pixmap is None or self._frame_size == (0, 0):
            self._display_size = (self.width(), self.height())
            return
        # 等比缩放适应控件
        pw, ph = self._frame_size
        cw, ch = max(1, self.width()), max(1, self.height())
        scale = min(cw / pw, ch / ph)
        self._display_size = (int(pw * scale), int(ph * scale))

    def _display_offset(self) -> Tuple[int, int]:
        dw, dh = self._display_size
        return ((self.width() - dw) // 2, (self.height() - dh) // 2)

    def _display_to_frame(self, pos: QPointF) -> Tuple[float, float]:
        """显示坐标 -> 原始帧像素坐标。"""
        self._recalc_display_size()
        dw, dh = self._display_size
        ox, oy = self._display_offset()
        pw, ph = self._frame_size if self._frame_size != (0, 0) else (dw, dh)
        if dw == 0 or dh == 0:
            return (float(pos.x()), float(pos.y()))
        fx = (pos.x() - ox) / dw * pw
        fy = (pos.y() - oy) / dh * ph
        return (fx, fy)

    # ---- 绘制 ----
    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if self._pixmap is None:
            return

        self._recalc_display_size()
        dw, dh = self._display_size
        ox, oy = self._display_offset()
        # 画帧
        painter.drawPixmap(ox, oy, dw, dh, self._pixmap)

        # 画已有 ROI
        for roi_pts in self._rois:
            self._draw_roi(painter, roi_pts, ox, oy, dw, dh, alarm=False)

        # 画违反目标红框
        pen = QPen(QColor(self._palette.alarm), 3)
        painter.setPen(pen)
        for (x1, y1, x2, y2) in self._violator_boxes:
            sx1 = ox + x1 / self._frame_size[0] * dw if self._frame_size[0] else x1
            sy1 = oy + y1 / self._frame_size[1] * dh if self._frame_size[1] else y1
            sx2 = ox + x2 / self._frame_size[0] * dw if self._frame_size[0] else x2
            sy2 = oy + y2 / self._frame_size[1] * dh if self._frame_size[1] else y2
            painter.setBrush(QBrush(QColor(217, 48, 37, 50)))
            painter.drawRect(int(sx1), int(sy1), int(sx2 - sx1), int(sy2 - sy1))

        # 画当前正在绘制的多边形
        if self._mode == self.MODE_DRAW and self._current_points:
            pts = [(p.x(), p.y()) for p in self._current_points]
            pen = QPen(QColor(self._palette.primary), 2, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            for i in range(len(pts) - 1):
                painter.drawLine(int(pts[i][0]), int(pts[i][1]), int(pts[i + 1][0]), int(pts[i + 1][1]))
            # 顶点
            painter.setBrush(QBrush(QColor(self._palette.primary)))
            for x, y in pts:
                painter.drawEllipse(QPointF(x, y), 4, 4)
            # 提示文字
            painter.setPen(QColor(self._palette.fg_main))
            painter.setFont(QFont("Microsoft YaHei", 10))
            painter.drawText(ox + 8, oy + 20, "点击添加顶点，双击/回车闭合，ESC 取消")

    def _draw_roi(self, painter: QPainter, roi_pts, ox, oy, dw, dh, alarm: bool) -> None:
        if not roi_pts:
            return
        pw, ph = self._frame_size if self._frame_size != (0, 0) else (dw, dh)
        poly = QPolygonF()
        for fx, fy in roi_pts:
            sx = ox + fx / pw * dw if pw else fx
            sy = oy + fy / ph * dh if ph else fy
            poly.append(QPointF(sx, sy))
        color = QColor(self._palette.alarm) if alarm else QColor(self._palette.primary)
        fill = QColor(color)
        fill.setAlpha(60)
        painter.setPen(QPen(color, 2))
        painter.setBrush(QBrush(fill))
        painter.drawPolygon(poly)

    # ---- 鼠标 ----
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._mode == self.MODE_DRAW and event.button() == Qt.LeftButton:
            self._current_points.append(event.pos())

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self._mode == self.MODE_DRAW:
            self._finalize_roi()

    def _finalize_roi(self) -> None:
        if len(self._current_points) >= 3:
            frame_pts = [self._display_to_frame(p) for p in self._current_points]
            self.roi_created.emit(frame_pts)
        self.cancel_drawing()

    # ---- 键盘 ----
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._mode == self.MODE_DRAW:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._finalize_roi()
                return
            if event.key() == Qt.Key_Escape:
                self.cancel_drawing()
                return
        super().keyPressEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._recalc_display_size()
        self.update()
