"""插件基类与上下文：插件系统与主程序之间的契约。

设计原则：
- 插件只依赖本文件定义的 Plugin 基类与 PluginContext，不直接 import 主程序的
  内部模块。这样主程序重构时，只要 PluginContext 的方法签名不变，插件就不用改。
- PluginContext 是「门面」：聚合主程序提供给插件的全部能力（配置、日志、路径、
  主题、检测器/统计/历史等核心服务），插件通过 ctx.xxx 访问，不需要自己去找单例。
- 插件按「需要什么才覆盖什么」实现：只有 create_widget 是必需的，其余事件回调
  （on_theme_changed / on_frame / on_alarm / on_shutdown）都是可选的，按需覆盖。

生命周期：
  1. PluginManager 发现插件 → 实例化 Plugin 子类（传入 PluginContext）
  2. create_widget() 创建页面 widget（由主程序加入导航 + 页面栈）
  3. 运行期主程序把事件回调分发给「订阅了该事件」的插件
  4. 程序退出前 on_shutdown() 释放资源
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from PyQt5.QtWidgets import QWidget
    from PyQt5.QtGui import QIcon
    from app.core.config_manager import ConfigManager
    from app.core.detector import Detector
    from app.core.statistics import StatsCollector
    from app.core.roi import RoiManager
    from app.core.history import HistoryManager
    from app.core.alarm import AlarmEngine, AlarmEvent
    from app.ui.theme import Palette
    import numpy as np


class PluginContext:
    """主程序提供给插件的「能力门面」。插件通过它访问主程序的服务。

    所有字段在插件加载时由 MainWindow 注入；插件运行期只读使用。
    避免把整个 MainWindow 暴露给插件（那样插件就能改主程序内部状态，耦合太重）。
    """

    def __init__(
        self,
        *,
        project_root: str,
        plugin_dir: str,
        config: "ConfigManager",
        palette: "Palette",
        detector: "Optional[Detector]" = None,
        roi_manager: "Optional[RoiManager]" = None,
        stats: "Optional[StatsCollector]" = None,
        history: "Optional[HistoryManager]" = None,
        alarm: "Optional[AlarmEngine]" = None,
        logger: Any = None,
        get_last_frame: "Optional[Callable[[], tuple | None]]" = None,
        show_status: "Optional[Callable[[str], None]]" = None,
    ) -> None:
        self.project_root = project_root   # 项目根目录（绝对路径）
        self.plugin_dir = plugin_dir       # 本插件所在目录（绝对路径）
        self.config = config               # 全局配置（ConfigManager）
        self.palette = palette             # 当前主题 Palette
        self.detector = detector           # YOLO 检测器（可能未加载完，用前判空）
        self.roi_manager = roi_manager     # ROI 区域管理
        self.stats = stats                 # 统计收集器
        self.history = history             # 历史记录管理
        self.alarm = alarm                 # 报警引擎
        self.logger = logger               # 结构化日志器
        self._get_last_frame = get_last_frame  # 获取最近一帧（annotated, violator_indices, centers）
        self._show_status = show_status    # 在主窗口状态栏显示一行文字

    # ---- 便捷方法（让插件少写判空）----
    def get_last_frame(self) -> "tuple | None":
        """获取最近一帧检测结果 (annotated, violator_indices, centers)，无则 None。"""
        return self._get_last_frame() if self._get_last_frame else None

    def show_status(self, text: str) -> None:
        """在主窗口底部状态栏显示一行文字（4 秒后由主程序清除规则管理）。"""
        if self._show_status:
            self._show_status(text)

    def abs_path(self, rel: str) -> str:
        """把相对项目根的路径转绝对路径；已是绝对路径则原样返回。"""
        import os
        if os.path.isabs(rel):
            return rel
        import os.path as osp
        return osp.join(self.project_root, rel)

    def plugin_path(self, rel: str) -> str:
        """把相对本插件目录的路径转绝对路径（插件自带资源用这个）。"""
        import os.path as osp
        return osp.join(self.plugin_dir, rel)


class Plugin:
    """插件基类。子类只需覆盖需要的部分。

    必需覆盖：
        create_widget()   -> QWidget  本插件的页面（加入导航 + 页面栈）
    可选覆盖（按需订阅事件）：
        on_load()                    页面已加入导航后调用，可做额外初始化
        on_theme_changed(palette)    主题切换时刷新自己的样式
        on_frame(annotated, vi, centers)  每个检测帧（仅当本页可见时分发，
                                     避免隐藏插件浪费 CPU）
        on_alarm(event)              报警事件
        on_shutdown()                程序退出前释放资源
    类属性（插件元信息，子类覆盖）：
        name    str   唯一标识（用于日志/排错，建议用英文短名）
        title   str   导航栏显示文字
        icon    str   图标：assets/icons 下的名字 或 插件目录下的 .svg 绝对路径
        nav_after str 可选：插在哪个内置页之后（"detection"/"roi"/"stats"/
                       "history"/"settings"）；默认追加到最后（在 settings 之后）
    """

    # 插件元信息（子类覆盖）
    name: str = "plugin"
    title: str = "插件"
    icon: str = ""        # 空则用默认方块图标
    nav_after: str = ""   # 空=追加到导航末尾

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx

    def create_widget(self) -> "QWidget":
        """创建并返回本插件的页面 widget。子类必须实现。"""
        raise NotImplementedError

    def on_load(self) -> None:
        """页面已加入导航后调用（可选）。"""

    def on_theme_changed(self, palette: "Palette") -> None:
        """主题切换时调用（可选）。"""

    def on_frame(self, annotated: "np.ndarray", violator_indices, centers) -> None:
        """每个检测帧到达时调用（可选；仅当本页可见时分发）。"""

    def on_alarm(self, event: "AlarmEvent") -> None:
        """报警事件（可选）。"""

    def on_shutdown(self) -> None:
        """程序退出前调用（可选，释放资源）。"""
