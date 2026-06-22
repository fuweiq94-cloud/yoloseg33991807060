"""视频播放对话框：用 cv2 读帧 + QTimer 定时刷新 QLabel 实现播放。

无 QtMultimedia 依赖（PyQt5 的多媒体支持在不同环境不一），用 cv2 + 定时器自实现，
保证跨环境可用。支持播放/暂停、进度拖动。
"""
from __future__ import annotations

import cv2
import numpy as np
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout, QSlider, QWidget,
)

from app.utils.logger import get_logger

logger = get_logger()


class VideoPlayerDialog(QDialog):
    """播放一个视频文件的模态对话框。"""

    def __init__(self, video_path: str, title: str = "视频回放", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(900, 600)

        self._path = video_path
        self._cap = cv2.VideoCapture(video_path)
        self._frame_count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 25.0
        self._current_idx = 0
        self._playing = False

        if not self._cap.isOpened():
            logger.error("无法打开视频: %s", video_path)

        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # 跳到首帧并显示
        self._seek(0)
        self._toggle_play()  # 默认开始播放

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 画面
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setMinimumSize(480, 320)
        self._label.setStyleSheet("background-color: #000;")
        layout.addWidget(self._label, 1)

        # 进度条
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(0, max(0, self._frame_count - 1))
        self._slider.sliderPressed.connect(self._on_slider_press)
        self._slider.sliderReleased.connect(self._on_slider_release)
        self._slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self._slider)

        # 控制行
        ctrl = QHBoxLayout()
        self._btn_play = QPushButton("暂停")
        self._btn_play.clicked.connect(self._toggle_play)
        self._btn_close = QPushButton("关闭")
        self._btn_close.clicked.connect(self.accept)
        self._lbl_info = QLabel()
        self._lbl_info.setStyleSheet("color: #9AA0A6;")
        ctrl.addWidget(self._btn_play)
        ctrl.addStretch(1)
        ctrl.addWidget(self._lbl_info)
        ctrl.addStretch(1)
        ctrl.addWidget(self._btn_close)
        layout.addLayout(ctrl)

    # ---- 播放循环 ----
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
        self._slider.blockSignals(True)
        self._slider.setValue(self._current_idx)
        self._slider.blockSignals(False)
        self._update_info()

    def _show_frame(self, frame_bgr: np.ndarray) -> None:
        if frame_bgr is None:
            return
        if not frame_bgr.flags["C_CONTIGUOUS"]:
            frame_bgr = np.ascontiguousarray(frame_bgr)
        h, w = frame_bgr.shape[:2]
        img = QImage(frame_bgr.data, w, h, w * 3, QImage.Format_BGR888).copy()
        pm = QPixmap.fromImage(img)
        # 缩放适应 label 尺寸（保持比例）
        scaled = pm.scaled(
            self._label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self._label.setPixmap(scaled)

    def _stop_at_end(self) -> None:
        self._timer.stop()
        self._playing = False
        self._btn_play.setText("重播")

    def _toggle_play(self) -> None:
        if self._current_idx >= self._frame_count - 1 and not self._playing:
            # 已到结尾，重播
            self._seek(0)
        if self._playing:
            self._timer.stop()
            self._playing = False
            self._btn_play.setText("播放")
        else:
            interval = int(1000 / self._fps) if self._fps > 0 else 40
            self._timer.start(interval)
            self._playing = True
            self._btn_play.setText("暂停")

    # ---- 拖动 ----
    def _on_slider_press(self) -> None:
        self._timer.stop()

    def _on_slider_release(self) -> None:
        if self._playing:
            interval = int(1000 / self._fps) if self._fps > 0 else 40
            self._timer.start(interval)

    def _on_slider_changed(self, value: int) -> None:
        # 拖动时跳转（仅在非播放推进时，避免与 _tick 互相干扰）
        if abs(value - self._current_idx) > 1:
            self._seek(value)

    def _seek(self, frame_idx: int) -> None:
        if not self._cap.isOpened():
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
            self._update_info()

    def _update_info(self) -> None:
        total_s = self._frame_count / self._fps if self._fps > 0 else 0
        cur_s = self._current_idx / self._fps if self._fps > 0 else 0
        self._lbl_info.setText(
            f"{cur_s:05.1f}s / {total_s:05.1f}s   ({self._current_idx}/{self._frame_count})"
        )

    def resizeEvent(self, event) -> None:
        # 窗口缩放时刷新当前帧的显示尺寸
        super().resizeEvent(event)
        if self._cap.isOpened():
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, self._current_idx))
            ok, frame = self._cap.read()
            if ok and frame is not None:
                self._current_idx = int(self._cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
                self._show_frame(frame)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        if self._cap is not None:
            self._cap.release()
        super().closeEvent(event)
