"""示例插件：插件系统用法展示 + 实时演示。

这个页面既是「插件系统能做什么」的活文档，也是一个可交互的演示：
  - 顶部：插件系统简介
  - 用法卡片：展示如何新建插件（可直接复制的代码片段）
  - API 卡片：列出 ctx 提供的全部能力
  - 实时演示：订阅 on_frame 显示帧计数，按钮演示 get_last_frame / show_status
  - 自带图标 icon.svg（演示插件目录资源的用法）

复制本目录改名即可开始写你自己的插件。
"""
from __future__ import annotations

from app.plugins.base import Plugin, PluginContext


# 可直接复制的「最小插件」代码（显示在用法卡片里）
_MIN_PLUGIN_CODE = '''from app.plugins.base import Plugin, PluginContext

class MyTool(Plugin):
    name = "my_tool"        # 唯一标识
    title = "我的工具"        # 导航栏文字
    icon = "icon.svg"       # 插件目录下的图标

    def create_widget(self):
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("我的插件页面"))
        return w

def create_plugin(ctx):     # 工厂函数（推荐）
    return MyTool(ctx)'''


# ctx 提供的能力清单（显示在 API 卡片里）
_API_ROWS = [
    ("ctx.config", "全局配置", "config.get('detection.device')"),
    ("ctx.detector", "YOLO 检测器", "用前判空，模型可能未加载"),
    ("ctx.stats", "统计收集器", "StatsCollector"),
    ("ctx.history", "历史记录", "HistoryManager"),
    ("ctx.alarm", "报警引擎", "AlarmEngine"),
    ("ctx.roi_manager", "ROI 区域", "RoiManager"),
    ("ctx.palette", "主题配色", "Palette"),
    ("ctx.get_last_frame()", "最近一帧", "(annotated, violators, centers)"),
    ("ctx.show_status(text)", "状态栏显示", "底部状态栏一行文字"),
    ("ctx.plugin_path(rel)", "插件资源", "插件目录下文件的绝对路径"),
]


class HelloPlugin(Plugin):
    name = "hello"            # 唯一标识（日志/排错用）
    title = "插件示例"          # 导航栏文字
    icon = "icon.svg"         # 本目录下的图标（相对插件目录）
    nav_after = ""            # 留空=追加到导航末尾

    def create_widget(self):
        """创建并返回本插件的页面 widget。"""
        from PyQt5.QtWidgets import (
            QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QFrame, QScrollArea, QSizePolicy,
        )
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QFont

        self._frame_count = 0  # 收到的帧数

        # 根容器用滚动区，内容多时不被截断
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(14)

        # ---- 标题 ----
        title = QLabel("🧩 插件系统")
        f = QFont(); f.setPointSize(16); f.setBold(True)
        title.setFont(f)
        v.addWidget(title)

        intro = QLabel(
            "插件系统让你在不改动源程序的前提下扩展功能。\n"
            "在 plugins/ 下新建一个目录 + plugin.py，启动后自动出现在左侧导航栏。\n"
            "本页就是一个插件——它的代码见 plugins/hello/plugin.py。"
        )
        intro.setProperty("role", "sub")
        intro.setWordWrap(True)
        v.addWidget(intro)

        # ---- 用法卡片：最小插件代码 ----
        v.addWidget(self._section_title("① 三步写一个插件"))
        steps = QLabel(
            "1. 在 plugins/ 下新建目录，比如 plugins/my_tool/\n"
            "2. 创建 plugin.py（代码如下）\n"
            "3. 启动程序——导航栏自动多出图标。删目录即卸载。"
        )
        steps.setWordWrap(True)
        v.addWidget(steps)
        code_lbl = QLabel(_MIN_PLUGIN_CODE)
        code_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        code_lbl.setStyleSheet(
            "background: #1e1e1e; color: #d4d4d4; font-family: Consolas, 'Courier New', monospace;"
            " font-size: 12px; padding: 10px; border-radius: 4px;"
        )
        v.addWidget(code_lbl)

        # ---- API 卡片：ctx 能力清单 ----
        v.addWidget(self._section_title("② 插件能调用什么（通过 self.ctx）"))
        for api, desc, example in _API_ROWS:
            row = QLabel(f"<b>{api}</b> &nbsp;—&nbsp; {desc} &nbsp;<span style='color:#888'>({example})</span>")
            row.setTextFormat(Qt.RichText)
            row.setWordWrap(True)
            v.addWidget(row)

        # ---- 事件订阅 ----
        v.addWidget(self._section_title("③ 订阅事件（按需覆盖基类方法）"))
        events = QLabel(
            "create_widget()   → 必需，返回页面 widget\n"
            "on_frame(frame, violators, centers)  → 每帧（仅本页可见时分发，不浪费 CPU）\n"
            "on_theme_changed(palette)  → 主题切换\n"
            "on_alarm(event)  → 报警事件\n"
            "on_load() / on_shutdown()  → 加载就绪 / 退出释放"
        )
        events.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 12px;"
            " background: #f5f5f5; padding: 10px; border-radius: 4px;"
        )
        v.addWidget(events)

        # ---- 实时演示卡片 ----
        v.addWidget(self._section_title("④ 实时演示"))
        self._card = QFrame()
        cl = QVBoxLayout(self._card)
        cl.setContentsMargins(12, 12, 12, 12)
        cl.setSpacing(8)
        # 从全局配置读检测设备（演示 ctx.config）
        try:
            device = self.ctx.config.get("detection.device", "cpu")
        except Exception:
            device = "?"
        dev_lbl = QLabel(f"当前检测设备（ctx.config 读）：{device}")
        dev_lbl.setStyleSheet("color: #555;")
        cl.addWidget(dev_lbl)
        self._lbl_stat = QLabel("收到的帧数：0\n当前目标数：0\n（开始检测后，本插件订阅 on_frame 实时累加）")
        self._lbl_stat.setStyleSheet("font-size: 14px;")
        cl.addWidget(self._lbl_stat)
        # 帧预览区
        self._lbl_preview = QLabel("（点「抓一帧」显示最近一帧检测结果）")
        self._lbl_preview.setAlignment(Qt.AlignCenter)
        self._lbl_preview.setMinimumHeight(220)
        self._lbl_preview.setStyleSheet("background-color: #000; color: #888; border-radius: 4px;")
        cl.addWidget(self._lbl_preview)
        # 按钮行
        row = QHBoxLayout()
        btn_frame = QPushButton("抓一帧（ctx.get_last_frame）")
        btn_frame.clicked.connect(self._on_grab_frame)
        btn_status = QPushButton("状态栏说话（ctx.show_status）")
        btn_status.clicked.connect(self._on_say_status)
        row.addWidget(btn_frame)
        row.addWidget(btn_status)
        row.addStretch(1)
        cl.addLayout(row)
        v.addWidget(self._card)

        v.addStretch(1)
        scroll.setWidget(inner)
        self._widget = scroll
        self._apply_card_style()
        return scroll

    def _section_title(self, text: str):
        from PyQt5.QtWidgets import QLabel
        from PyQt5.QtGui import QFont
        lbl = QLabel(text)
        f = QFont(); f.setPointSize(11); f.setBold(True)
        lbl.setFont(f)
        return lbl

    # ---- 按钮回调 ----
    def _on_grab_frame(self):
        """用 ctx.get_last_frame() 拿最近一帧并显示。"""
        from PyQt5.QtGui import QImage, QPixmap
        from PyQt5.QtCore import Qt
        last = self.ctx.get_last_frame()
        if last is None:
            self.ctx.show_status("[hello] 还没有检测帧（先开始检测）")
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
        self._lbl_stat.setText(
            f"收到的帧数：{self._frame_count}\n"
            f"当前目标数：{n}\n"
            f"（开始检测后，本插件订阅 on_frame 实时累加）"
        )

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
