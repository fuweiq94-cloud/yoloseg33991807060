"""视频内联播放控件：播放/暂停按钮 + 可拖动进度条 + 时间显示。

设计参考 video_player_dialog.py 的 slider 拖动模式：
- sliderPressed / sliderReleased 标记用户是否在拖动，拖动期间不接受
  worker 推来的位置更新（避免进度条回弹），松手恢复。
- valueChanged 仅在「跳变 > 1」时触发 seek，避免播放逐帧推进时把每一帧
  都当成 seek 请求（那样会反复重置 tracker，性能差且画面抖）。

进度位置完全由 worker 的 progress_updated 信号驱动（非 UI 侧计时），
保证进度条与真实推理帧严格一致。

复用 assets/icons/start.svg（三角形 ▶）作播放图标、pause.svg（两竖条）作暂停图标。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, QSize, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QSlider, QLabel,
)

from app.ui.theme import get_palette
from app.ui.widgets.svg_icon import load_svg_icon


def _fmt(seconds: float) -> str:
    """00:00 / 00:00 格式化。负数或 NaN 兜底成 0。"""
    try:
        if seconds < 0 or seconds != seconds:  # NaN check
            seconds = 0.0
    except Exception:
        seconds = 0.0
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


class VideoPlaybackBar(QWidget):
    """检测页/ROI 页画布下方的视频播放控件。

    信号：
    - play_toggled()：用户点击播放/暂停按钮（切换状态）。MainWindow 据此调
      worker.pause()/resume()，并通过 set_playing() 把结果状态回灌回来。
    - seek_requested(int frame_idx)：用户拖动进度条产生的跳转请求。

    只对「视频文件源」显示；相机/图片源由页面 set_video_mode(False) 隐藏。
    """

    play_toggled = pyqtSignal()
    seek_requested = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._frame_count: int = 0
        self._fps: float = 0.0
        self._playing: bool = True   # 默认识别一开始就在播放
        # 用户正在拖动滑块：期间屏蔽 worker 推来的位置，避免回弹
        self._user_dragging: bool = False
        self._build()
        self._apply_theme()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(8)

        self._btn_play = QPushButton()
        self._btn_play.setCheckable(False)
        self._btn_play.setFixedSize(32, 28)
        self._btn_play.setIconSize(QSize(16, 16))
        self._btn_play.clicked.connect(self.play_toggled.emit)
        layout.addWidget(self._btn_play)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.sliderPressed.connect(self._on_slider_press)
        self._slider.sliderReleased.connect(self._on_slider_release)
        self._slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self._slider, 1)

        self._lbl_time = QLabel("00:00 / 00:00")
        self._lbl_time.setMinimumWidth(110)
        self._lbl_time.setAlignment(Qt.AlignCenter)
        self._lbl_time.setStyleSheet("color: #9AA0A6; font-size: 12px;")
        layout.addWidget(self._lbl_time)

        self._refresh_button_icon()

    def _apply_theme(self) -> None:
        pal = get_palette()
        self._btn_play.setStyleSheet(
            f"QPushButton {{ background: {pal.bg_input}; border: 1px solid {pal.border}; "
            f"border-radius: 4px; }}"
            f"QPushButton:hover {{ background: {pal.primary_lo}; }}"
        )

    # ---- 外部驱动：位置 / 状态 ----
    def set_range(self, frame_count: int, fps: float) -> None:
        """设置进度条范围与帧率（用于时间换算）。frame_count<=0 时禁用控件。"""
        self._frame_count = max(0, frame_count)
        self._fps = float(fps) if fps and fps > 0 else 0.0
        # range 末尾用 frame_count（位置语义：已处理到第 N 帧，N 可达 total）
        self._slider.setRange(0, self._frame_count)
        self._update_time(self._slider.value())

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def set_position(self, frame_idx: int) -> None:
        """worker 推进时调用，更新进度条位置。用户正在拖动时不更新（防回弹）。"""
        if self._user_dragging:
            return
        # blockSignals 防止 setValue 触发 valueChanged → seek_requested 回环
        self._slider.blockSignals(True)
        self._slider.setValue(int(frame_idx))
        self._slider.blockSignals(False)
        self._update_time(int(frame_idx))

    def set_playing(self, playing: bool) -> None:
        """更新播放/暂停按钮状态（由 MainWindow 在 pause/resume 后回灌）。"""
        self._playing = playing
        self._refresh_button_icon()

    def reset(self) -> None:
        """重置到初始状态（新一轮识别前）。"""
        self._user_dragging = False
        self._playing = True
        self._slider.blockSignals(True)
        self._slider.setValue(0)
        self._slider.blockSignals(False)
        self._update_time(0)
        self._refresh_button_icon()

    # ---- 用户交互 ----
    def _on_slider_press(self) -> None:
        self._user_dragging = True

    def _on_slider_release(self) -> None:
        # 松手：以当前值为目标 seek
        self._user_dragging = False
        target = self._slider.value()
        self._update_time(target)
        # 即便跳变 <=1 也发 seek：松手代表明确的用户意图
        if self._frame_count > 0:
            self.seek_requested.emit(target)

    def _on_slider_changed(self, value: int) -> None:
        # 仅在「跳变 > 1」时 seek，避免逐帧推进（差 1）被误判为 seek。
        # 拖动过程中连续触发会让 worker 反复重置 tracker，性能差且画面抖。
        if self._user_dragging:
            self._update_time(value)
            return
        if self._frame_count > 0 and abs(value - self._last_emitted_pos()) > 1:
            self.seek_requested.emit(value)
        self._update_time(value)

    def _last_emitted_pos(self) -> int:
        return getattr(self, "_last_seek_pos", -999)

    def _refresh_button_icon(self) -> None:
        pal = get_palette()
        # 播放中显示「暂停」图标（点击会暂停）；暂停时显示「播放」图标
        name = "pause" if self._playing else "start"
        self._btn_play.setIcon(load_svg_icon(name, pal.fg_main, 16))
        self._btn_play.setToolTip("暂停" if self._playing else "播放")

    def _update_time(self, frame_idx: int) -> None:
        cur = frame_idx / self._fps if self._fps > 0 else 0.0
        total = self._frame_count / self._fps if self._fps > 0 else 0.0
        self._lbl_time.setText(f"{_fmt(cur)} / {_fmt(total)}")
        # 记录上次 seek 位置（_on_slider_changed 用来判断跳变）
        self._last_seek_pos = frame_idx

    def changeEvent(self, event) -> None:
        # 主题切换时刷新按钮样式（基类会在事件类型变化时回调）
        super().changeEvent(event)
        try:
            self._apply_theme()
        except Exception:
            pass
