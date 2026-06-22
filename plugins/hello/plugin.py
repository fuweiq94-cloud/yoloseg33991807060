"""示例插件：演示插件系统能力的完整模板。

这个插件做这些事：
  - 页面顶部显示插件名 + 当前检测设备（从 ctx.config 读）
  - 实时统计：每收一帧就累加计数 + 更新「目标数」（订阅 on_frame）
  - 「抓一帧」按钮：用 ctx.get_last_frame() 拿最近一帧并显示
  - 「在状态栏说话」按钮：演示 ctx.show_status()
  - 主题切换时自动改卡片背景色（on_theme_changed）

复制本目录改名即可开始写你自己的插件。最小可用插件只需 create_widget()。
"""
from __future__ import annotations

from app.plugins.base import Plugin, PluginContext


class HelloPlugin(Plugin):
    name = "hello"          # 唯一标识（日志/排错用）
    title = "插件示例"        # 导航栏文字
    icon = "icon.svg"       # 本目录下的图标（相对插件目录）；也可填 assets/icons 下的名字
    nav_after = ""          # 留空=追加到导航末尾

    def create_widget(self):
        """创建并返回本插件的页面 widget。"""
        from PyQt5.QtWidgets import (
            QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
        )
        from PyQt5.QtCore import Qt

        self._frame_count = 0  # 收到的帧数

        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        # ---- 标题 + 设备信息 ----
        title = QLabel("🧩 插件示例")
        title.setProperty("role", "title")
        v.addWidget(title)

        # 从全局配置读检测设备（演示 ctx.config 用法）
        device = self.ctx.config.get("detection.device", "cpu")
        model = self.ctx.config.get("detection.model_path", "?")
        info = QLabel(f"当前检测设备：{device}\n模型：{model}")
        info.setProperty("role", "sub")
        info.setWordWrap(True)
        v.addWidget(info)

        # ---- 实时统计卡片（on_frame 会更新它）----
        self._card = QFrame()
        self._card_layout = QVBoxLayout(self._card)
        self._card_layout.setContentsMargins(12, 12, 12, 12)
        self._lbl_stat = QLabel("收到的帧数：0\n当前目标数：0")
        self._lbl_stat.setStyleSheet("font-size: 16px;")
        self._card_layout.addWidget(self._lbl_stat)
        v.addWidget(self._card)
        self._apply_card_style()

        # ---- 帧预览区 ----
        self._lbl_preview = QLabel("（点「抓一帧」显示最近一帧）")
        self._lbl_preview.setAlignment(Qt.AlignCenter)
        self._lbl_preview.setMinimumHeight(240)
        self._lbl_preview.setStyleSheet("background-color: #000; color: #888;")
        v.addWidget(self._lbl_preview, 1)

        # ---- 按钮行 ----
        row = QHBoxLayout()
        btn_frame = QPushButton("抓一帧")
        btn_frame.clicked.connect(self._on_grab_frame)
        btn_status = QPushButton("在状态栏说话")
        btn_status.clicked.connect(self._on_say_status)
        row.addWidget(btn_frame)
        row.addWidget(btn_status)
        row.addStretch(1)
        v.addLayout(row)

        v.addStretch(1)
        self._widget = w
        return w

    # ---- 按钮回调 ----
    def _on_grab_frame(self):
        """用 ctx.get_last_frame() 拿最近一帧并显示。"""
        from PyQt5.QtGui import QImage, QPixmap
        from PyQt5.QtCore import Qt
        last = self.ctx.get_last_frame()
        if last is None:
            self.ctx.show_status("还没有检测帧（先开始检测）")
            return
        annotated, _violators, _centers = last
        h, w = annotated.shape[:2]
        img = QImage(annotated.data, w, h, w * 3, QImage.Format_BGR888).copy()
        pm = QPixmap.fromImage(img).scaled(
            self._lbl_preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self._lbl_preview.setPixmap(pm)

    def _on_say_status(self):
        """演示 ctx.show_status()。"""
        self.ctx.show_status(f"[hello 插件] 已处理 {self._frame_count} 帧")

    # ---- 事件订阅（按需覆盖）----
    def on_frame(self, annotated, violator_indices, centers):
        """每帧到达（仅本页可见时分发）。累加计数 + 更新目标数。"""
        self._frame_count += 1
        try:
            n = len(centers) if centers is not None else 0
        except TypeError:
            n = 0
        self._lbl_stat.setText(f"收到的帧数：{self._frame_count}\n当前目标数：{n}")

    def on_theme_changed(self, palette):
        """主题切换时刷新卡片背景。"""
        self._apply_card_style(palette)

    def on_shutdown(self):
        if self.ctx.logger:
            self.ctx.logger.info("[hello] 插件退出，共处理 %d 帧", self._frame_count)

    # ---- 辅助 ----
    def _apply_card_style(self, palette=None):
        p = palette or self.ctx.palette
        self._card.setStyleSheet(
            f"QFrame {{ background: {p.bg_panel}; border: 1px solid {p.border}; border-radius: 6px; }}"
        )


# 工厂函数：插件管理器优先调用它（需要 ctx 才能构造的场景用这个最稳）
def create_plugin(ctx: PluginContext) -> HelloPlugin:
    return HelloPlugin(ctx)
