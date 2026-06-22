"""历史预览面板：嵌入在历史页内的图片/视频预览（不再弹独立窗口）。

复用 video_player_dialog 的播放机制（cv2 读帧 + QTimer 定时刷新 QLabel），
但作为嵌入式 QWidget 而非模态 QDialog，与左侧缩略图网格同屏共存。

- 图片：直接显示，按面板尺寸等比缩放。
- 视频：播放/暂停 + 可拖动进度条 + 时间显示，支持 seek。
- 空状态：显示占位提示。

无 QtMultimedia 依赖（PyQt5 多媒体支持在不同环境不一），用 cv2 + 定时器自实现。
"""
from __future__ import annotations

import cv2
import numpy as np
from PyQt5.QtCore import Qt, QTimer, QSize
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider,
)

from app.ui.theme import get_palette
from app.ui.widgets.svg_icon import load_svg_icon
from app.utils.logger import get_logger

logger = get_logger()


def _fmt(seconds: float) -> str:
    try:
        if seconds < 0 or seconds != seconds:  # NaN
            seconds = 0.0
    except Exception:
        seconds = 0.0
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


class HistoryPreviewPane(QWidget):
    """历史页右侧的内嵌预览面板。

    由 HistoryPage.open_record() 驱动：show_image(path) 或 play_video(path, title)。
    切换源前会先 clear() 释放上一个视频的 cv2 资源与定时器。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cap: cv2.VideoCapture | None = None
        self._frame_count: int = 0
        self._fps: float = 0.0
        self._current_idx: int = 0
        self._playing: bool = False
        self._mode: str = "empty"   # empty / image / video
        self._title: str = ""
        # 用户正在拖动滑块：期间屏蔽 _tick 的位置回写，防回弹
        self._user_dragging: bool = False
        # 图片模式：缓存原图供 resizeEvent 重新缩放
        self._last_pm: QPixmap = QPixmap()

        self._build_ui()
        self._apply_theme()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self.show_empty()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        # 标题行
        self._lbl_title = QLabel("")
        self._lbl_title.setProperty("role", "title")
        self._lbl_title.setAlignment(Qt.AlignCenter)
        v.addWidget(self._lbl_title)

        # 显示区：QLabel 黑底，居中
        self._display = QLabel()
        self._display.setAlignment(Qt.AlignCenter)
        self._display.setMinimumSize(320, 240)
        self._display.setStyleSheet("background-color: #000;")
        v.addWidget(self._display, 1)

        # 播放控件行（仅视频模式显示）
        self._ctrl_row = QWidget()
        ctrl = QHBoxLayout(self._ctrl_row)
        ctrl.setContentsMargins(0, 0, 0, 0)
        ctrl.setSpacing(8)

        self._btn_play = QPushButton()
        self._btn_play.setFixedSize(34, 28)
        self._btn_play.setIconSize(QSize(16, 16))
        self._btn_play.clicked.connect(self._toggle_play)
        ctrl.addWidget(self._btn_play)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.sliderPressed.connect(self._on_slider_press)
        self._slider.sliderReleased.connect(self._on_slider_release)
        self._slider.valueChanged.connect(self._on_slider_changed)
        ctrl.addWidget(self._slider, 1)

        self._lbl_time = QLabel("00:00 / 00:00")
        self._lbl_time.setMinimumWidth(115)
        self._lbl_time.setAlignment(Qt.AlignCenter)
        self._lbl_time.setStyleSheet("color: #9AA0A6; font-size: 12px;")
        ctrl.addWidget(self._lbl_time)

        self._ctrl_row.setVisible(False)
        v.addWidget(self._ctrl_row)

        self._refresh_play_icon()

    def _apply_theme(self) -> None:
        pal = get_palette()
        self._btn_play.setStyleSheet(
            f"QPushButton {{ background: {pal.bg_input}; border: 1px solid {pal.border}; "
            f"border-radius: 4px; }}"
            f"QPushButton:hover {{ background: {pal.primary_lo}; }}"
        )

    # ================================================================
    # 外部入口：HistoryPage.open_record 调用
    # ================================================================
    def show_image(self, path: str, title: str = "") -> None:
        """显示一张图片。"""
        self.clear()
        self._mode = "image"
        self._title = title
        self._lbl_title.setText(title)
        pm = QPixmap(path)
        if pm.isNull():
            self._display.setText("图片加载失败")
            self._display.setPixmap(QPixmap())
            self._last_pm = QPixmap()
            return
        self._last_pm = pm   # 保存原图供 resizeEvent 重新缩放
        self._fit_pixmap(pm)
        self._ctrl_row.setVisible(False)

    def play_video(self, path: str, title: str = "") -> None:
        """开始播放一个视频。默认从首帧自动播放。"""
        self.clear()
        self._mode = "video"
        self._title = title
        self._lbl_title.setText(title)
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            logger.error("无法打开视频: %s", path)
            self._display.setText("视频打开失败")
            self._ctrl_row.setVisible(False)
            return
        self._frame_count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 25.0
        self._slider.setRange(0, max(0, self._frame_count - 1))
        self._ctrl_row.setVisible(True)
        self._playing = True
        self._refresh_play_icon()
        # 跳到首帧显示，再启动定时器
        self._seek(0)
        self._start_timer()

    def clear(self) -> None:
        """释放当前资源（视频 cv2 / 定时器），回到空状态前的过渡。
        空状态由 _show_empty() 单独负责。"""
        if self._timer.isActive():
            self._timer.stop()
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._frame_count = 0
        self._fps = 0.0
        self._current_idx = 0
        self._playing = False
        self._user_dragging = False
        self._slider.blockSignals(True)
        self._slider.setValue(0)
        self._slider.blockSignals(False)

    def show_empty(self) -> None:
        """空状态占位（公开入口，供页面在删除/清空后复位预览）。"""
        self.clear()
        self._mode = "empty"
        self._title = ""
        self._lbl_title.setText("预览")
        self._display.setText("双击左侧记录查看预览")
        self._display.setPixmap(QPixmap())
        self._last_pm = QPixmap()
        self._ctrl_row.setVisible(False)

    # ================================================================
    # 视频播放循环（复用 video_player_dialog 逻辑）
    # ================================================================
    def _start_timer(self) -> None:
        if self._fps > 0:
            self._timer.start(int(1000 / self._fps))
        else:
            self._timer.start(40)

    def _tick(self) -> None:
        if self._current_idx >= self._frame_count - 1:
            self._stop_at_end()
            return
        ok, frame = self._cap.read()
        if not ok:
            self._stop_at_end()
            return
        self._current_idx += 1
        self._show_frame(frame)
        if not self._user_dragging:
            self._slider.blockSignals(True)
            self._slider.setValue(self._current_idx)
            self._slider.blockSignals(False)
        self._update_time()

    def _show_frame(self, frame_bgr: np.ndarray) -> None:
        if frame_bgr is None:
            return
        if not frame_bgr.flags["C_CONTIGUOUS"]:
            frame_bgr = np.ascontiguousarray(frame_bgr)
        h, w = frame_bgr.shape[:2]
        img = QImage(frame_bgr.data, w, h, w * 3, QImage.Format_BGR888).copy()
        pm = QPixmap.fromImage(img)
        self._fit_pixmap(pm)

    def _fit_pixmap(self, pm: QPixmap) -> None:
        """按面板尺寸等比缩放（保持比例）。"""
        sz = self._display.size()
        if sz.width() < 2 or sz.height() < 2:
            # 面板还没布局好，直接显示原图
            self._display.setPixmap(pm)
            return
        self._display.setPixmap(pm.scaled(sz, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _stop_at_end(self) -> None:
        self._timer.stop()
        self._playing = False
        self._refresh_play_icon()

    def _toggle_play(self) -> None:
        if self._cap is None:
            return
        # 已到结尾再点播放：从头重播
        if self._current_idx >= self._frame_count - 1 and not self._playing:
            self._seek(0)
        if self._playing:
            self._timer.stop()
            self._playing = False
        else:
            self._start_timer()
            self._playing = True
        self._refresh_play_icon()

    def _refresh_play_icon(self) -> None:
        pal = get_palette()
        # 播放中显示「暂停」图标（点击会暂停）；暂停/结尾时显示「播放」图标
        name = "pause" if self._playing else "start"
        self._btn_play.setIcon(load_svg_icon(name, pal.fg_main, 16))
        self._btn_play.setToolTip("暂停" if self._playing else "播放")

    # ================================================================
    # 拖动 / seek
    # ================================================================
    def _on_slider_press(self) -> None:
        self._user_dragging = True
        self._timer.stop()

    def _on_slider_release(self) -> None:
        self._user_dragging = False
        self._seek(self._slider.value())
        if self._playing:
            self._start_timer()

    def _on_slider_changed(self, value: int) -> None:
        # 仅拖动过程中跳变 >1 时实时 seek（避免逐帧推进被误判）
        if self._user_dragging and abs(value - self._current_idx) > 1:
            self._seek(value)
        elif self._user_dragging:
            # 拖动时即使差 1 也更新时间显示，给用户即时反馈
            pass
        self._update_time()

    def _seek(self, frame_idx: int) -> None:
        if self._cap is None or not self._cap.isOpened():
            return
        frame_idx = max(0, min(frame_idx, max(0, self._frame_count - 1)))
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = self._cap.read()
        if ok and frame is not None:
            self._current_idx = frame_idx
            self._show_frame(frame)
            self._slider.blockSignals(True)
            self._slider.setValue(frame_idx)
            self._slider.blockSignals(False)
            self._update_time()

    def _update_time(self) -> None:
        total_s = self._frame_count / self._fps if self._fps > 0 else 0.0
        cur_s = self._current_idx / self._fps if self._fps > 0 else 0.0
        self._lbl_time.setText(f"{_fmt(cur_s)} / {_fmt(total_s)}")

    # ================================================================
    # 事件
    # ================================================================
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 面板尺寸变化时刷新当前显示（按新尺寸重新缩放）
        if self._mode == "video" and self._cap is not None and self._cap.isOpened():
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, self._current_idx))
            ok, frame = self._cap.read()
            if ok and frame is not None:
                self._current_idx = int(self._cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
                self._show_frame(frame)
        elif self._mode == "image":
            # 图片：重新加载需要原图，这里只在已保存 last pm 时才缩放。
            pm = getattr(self, "_last_pm", None)
            if pm is not None and not pm.isNull():
                self._fit_pixmap(pm)
        # 注意：_fit_pixmap 路径下 show_image 调用时已设 _last_pm

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        try:
            self._apply_theme()
        except Exception:
            pass
