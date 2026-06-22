"""主窗口：左侧导航 + 顶部常驻工具栏 + 多页面（QStackedWidget）。

本窗口只做「协调与路由」：
- 持有所有核心服务（detector / roi_manager / stats / alarm / worker）
- 装配：顶部工具栏（常驻）| 左侧导航 | 右侧页面栈 | 底部状态栏
- 连线 worker 信号 → 路由到对应页面：
    frame_ready   -> 检测页画布 + ROI 页画布（两个画布共享同一帧）
    stats_ready   -> 统计页
    alarm_ready   -> 统计页异常帧 + 状态灯
- 报警四通道（visual/sound/snapshot/log）在此落地
- 用户操作（ROI 绘制/导入导出、类别筛选、设置应用）通过页面信号回传

各页面在 app/ui/pages/ 下独立成文件。
"""
from __future__ import annotations

import csv
import os
from datetime import datetime

import numpy as np
from PyQt5.QtCore import Qt, QTimer, QSize, pyqtSignal
from PyQt5.QtGui import QIcon, QColor
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QLabel,
    QPushButton, QListWidget, QListWidgetItem, QStackedWidget,
    QMessageBox, QFileDialog, QMenu, QAction, QToolBar, QFrame,
    QSizePolicy,
)
from PyQt5.QtMultimedia import QSound

from app.core.config_manager import get_config
from app.core.detector import Detector
from app.core.roi import RoiManager
from app.core.statistics import StatsCollector
from app.core.alarm import AlarmEngine, AlarmConfig, AlarmEvent
from app.utils.logger import get_logger
from app.ui.theme import get_palette, apply_theme, set_titlebar_color
from app.ui.widgets.control_bar import ControlBar, SourceType
from app.ui.widgets.anomaly_list import AnomalyItem
from app.ui.pages.detection_page import DetectionPage
from app.ui.pages.roi_page import RoiPage
from app.ui.pages.stats_page import StatsPage
from app.ui.pages.history_page import HistoryPage
from app.ui.pages.settings_page import SettingsPage
from app.ui.dialogs.about_dialog import AboutDialog
from app.ui.widgets.class_filter import load_classes
from app.plugins import PluginManager, PluginContext, Plugin

logger = get_logger()


class MainWindow(QMainWindow):
    # 跨线程信号：把 AlarmEvent 从工作线程转发到主线程处理 UI
    _visual_alarm = pyqtSignal(object)
    # 跨线程信号：报警声音必须在主线程播放（QSound 是 QObject，在工作线程
    # 创建子对象会触发 "Cannot create children for a parent that is in a
    # different thread"），用信号把播放请求转发到主线程。
    _sound_alarm = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("MainWindow")
        self.setWindowTitle("安全识别系统 SecurityVisionSystem")
        self.resize(1320, 860)

        # ---- 核心服务 ----
        self._cfg = get_config()
        self._project_root = self._resolve_project_root()
        self._roi_manager = RoiManager()
        self._stats = StatsCollector(
            logs_dir=self._abs_path(self._cfg.get("paths.logs_dir", "data/logs")),
        )
        self._detector: Detector | None = None  # 懒加载
        self._alarm: AlarmEngine | None = None
        self._worker = None  # VideoWorker / ImageWorker

        # 类别元数据
        self._classes_meta = load_classes(
            os.path.join(self._project_root, "config", "classes.json")
        )

        # 主题
        self._theme = self._cfg.get("appearance.theme", "dark")
        self._palette = get_palette(self._theme)

        # 状态灯闪烁
        self._alarm_glow = False
        self._alarm_timer = QTimer(self)
        self._alarm_timer.setInterval(600)
        self._alarm_timer.timeout.connect(self._blink_alarm)

        # 时钟
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start()

        # 统计刷新节流：worker 每帧 emit stats_ready，但全量查询(_samples 遍历)
        # 很贵。这里用 1Hz 定时器统一刷新，_on_stats 只标记脏数据。
        # 同一定时器顺带刷新 FPS/目标数状态文字（每帧 setText 无意义且昂贵）。
        self._stats_dirty = False
        self._status_dirty = False
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._refresh_periodic)
        self._stats_timer.start()

        # 配置变更监听
        self._cfg.add_listener(self._on_config_changed)

        # 跨线程报警 -> 主线程
        self._visual_alarm.connect(self._handle_visual_alarm)
        self._sound_alarm.connect(self._play_alarm_sound)

        # 当前状态（供状态灯/状态条读数）
        self._cur_fps = 0.0
        self._cur_objs = 0
        self._cur_alarm = False
        # 最近一帧缓存：ROI 页按需显示时补帧用（避免给隐藏画布每帧做昂贵转换）
        self._last_frame: tuple | None = None  # (annotated, violator_indices, centers)

        # 历史记录管理
        from app.core.history import HistoryManager
        hist_dir = self._abs_path(self._cfg.get("paths.history_dir", "data/history"))
        self._history = HistoryManager(history_dir=hist_dir)
        self._history.cleanup_temp()  # 启动时清理上次残留的临时录制文件
        # 当前视频录制状态：识别视频文件时，临时路径就绪后供「保存」归档
        self._cur_record: tuple | None = None  # (tmp_path, duration, source_name)
        # 当前源类型/名称（供保存时记录来源）
        self._cur_source_name: str = ""
        self._cur_source_type: object = None

        # 插件系统：在 __init__ 末尾加载（nav/stack/lbl_status 全部就绪后）
        self._plugin_mgr: PluginManager | None = None
        # 已加载的插件列表（_LoadedPlugin），用于事件分发
        self._plugin_pages: list = []

        self._build_bottom_toolbar()
        # 隐藏系统原生菜单栏：改用底部工具条承载同样的快捷操作
        self.menuBar().setVisible(False)
        self._build_central()
        self._build_statusbar()
        self._apply_statusbar_state()  # 默认显示状态栏

        # 加载插件：必须在 _build_central（nav/stack 就绪）+ _build_statusbar
        # （lbl_status 就绪，PluginContext 依赖它）之后。
        self._load_plugins()

        # 启动后异步初始化检测器
        QTimer.singleShot(50, self._lazy_init_detector)

    # ---- 路径 ----
    @staticmethod
    def _resolve_project_root() -> str:
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _abs_path(self, rel: str) -> str:
        if os.path.isabs(rel):
            return rel
        return os.path.join(self._project_root, rel)

    def _ensure_dir(self, rel: str) -> str:
        abs_path = self._abs_path(rel)
        try:
            os.makedirs(abs_path, exist_ok=True)
        except OSError:
            pass
        return abs_path

    # ====================================================================
    # 装配
    # ====================================================================
    def _build_bottom_toolbar(self) -> None:
        """底部快捷工具条：页面跳转 + 状态栏开关 + 关于。

        替代原顶部菜单栏（页面/视图/帮助）。作为 QMainWindow 的工具栏，
        默认加在顶部；构造后通过 setToolBarArea 移到底部（Qt.BottomToolBarArea），
        使其紧贴状态栏上方。
        """
        tb = QToolBar("快捷")
        tb.setObjectName("BottomToolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(Qt.BottomToolBarArea, tb)

        # 页面跳转（对应原「页面」菜单）
        for idx, name in enumerate(("检测", "ROI 区域", "统计", "历史记录", "设置")):
            act = QAction(name, self)
            act.triggered.connect(lambda _checked=False, i=idx: self._goto_page(i))
            tb.addAction(act)

        tb.addSeparator()

        # 视图：状态栏开关（对应原「视图」菜单）
        self.act_toggle_statusbar = QAction("状态栏", self, checkable=True)
        self.act_toggle_statusbar.triggered.connect(self._toggle_statusbar)
        tb.addAction(self.act_toggle_statusbar)

        tb.addSeparator()

        # 帮助：关于（对应原「帮助」菜单）
        act_about = QAction("关于", self)
        act_about.triggered.connect(lambda: AboutDialog(self).exec_())
        tb.addAction(act_about)

        tb.addSeparator()
        # 右侧弹簧把后面的内容推到最右（留空，保持简洁）
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)

        self._bottom_toolbar = tb

    def _build_central(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 顶部常驻工具栏
        self.control_bar = ControlBar(self)
        self.control_bar.start_requested.connect(self._on_start)
        self.control_bar.pause_requested.connect(self._on_pause)
        self.control_bar.resume_requested.connect(self._on_resume)
        self.control_bar.stop_requested.connect(self._on_stop)
        self.control_bar.save_requested.connect(self._on_save)
        self.control_bar.source_type_changed.connect(self._on_source_type_changed)
        toolbar_wrap = QWidget()
        th = QHBoxLayout(toolbar_wrap)
        th.setContentsMargins(0, 0, 0, 0)
        th.addWidget(self.control_bar)
        root.addWidget(toolbar_wrap)

        # 主体：左导航 | 右页面栈
        body = QSplitter(Qt.Horizontal)

        # 左侧导航（可折叠：折叠态仅显示图标）—— 默认折叠
        self._nav_collapsed = True
        self._nav_width_expanded = 170
        self._nav_width_collapsed = 56
        nav_container = QWidget()
        nav_container.setObjectName("NavContainer")
        nav_v = QVBoxLayout(nav_container)
        nav_v.setContentsMargins(0, 0, 0, 0)
        nav_v.setSpacing(0)

        # 折叠/展开按钮
        # 折叠/展开按钮：默认折叠态显示 »（点击展开）
        self.btn_toggle_nav = QPushButton("»" if self._nav_collapsed else "«")
        self.btn_toggle_nav.setObjectName("NavToggle")
        self.btn_toggle_nav.setProperty("role", "flat")
        self.btn_toggle_nav.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_nav.setFixedHeight(28)
        self.btn_toggle_nav.clicked.connect(self._toggle_nav)
        nav_v.addWidget(self.btn_toggle_nav)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        icon_px = self.nav.fontMetrics().height() + 8
        self._nav_icon_px = icon_px   # 供 _plugin_nav_icon 渲染时对齐尺寸
        self.nav.setIconSize(QSize(icon_px, icon_px))
        self.nav.setSpacing(2)
        self.nav.setStyleSheet(
            "QListWidget#NavList { padding: 6px 4px; border: none; }"
            "QListWidget#NavList::item { padding: 10px 10px; border-radius: 4px; }"
            f"QListWidget#NavList::item:selected {{ background-color: {self._palette.primary}; color: #FFFFFF; }}"
            f"QListWidget#NavList::item:hover {{ background-color: {self._palette.bg_input}; }}"
        )
        self._nav_icon_px = icon_px
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        nav_v.addWidget(self.nav)

        self._nav_container = nav_container
        nav_container.setFixedWidth(
            self._nav_width_collapsed if self._nav_collapsed else self._nav_width_expanded
        )
        body.addWidget(nav_container)

        # 右侧页面栈
        self.stack = QStackedWidget()
        self.page_detection = DetectionPage(
            self._classes_meta,
            list(self._cfg.get("detection.classes", [0])),
        )
        self.page_roi = RoiPage()
        self.page_stats = StatsPage()
        self.page_history = HistoryPage()
        self.page_settings = SettingsPage(self._cfg)

        # 页面 -> (图标文件名)
        icon_map = {
            self.page_detection: "detection",
            self.page_roi: "roi",
            self.page_stats: "stats",
            self.page_history: "history",
            self.page_settings: "settings",
        }
        for page in (self.page_detection, self.page_roi, self.page_stats, self.page_history, self.page_settings):
            self.stack.addWidget(page)
            # 折叠态清空文字（仅留图标），展开态显示标题
            item = QListWidgetItem("" if self._nav_collapsed else page.title)
            item.setData(Qt.UserRole, icon_map[page])           # 图标名
            item.setData(Qt.UserRole + 1, page.title)           # 标题（折叠时清空文本、展开时恢复）
            item.setIcon(self._nav_icon(icon_map[page], selected=False))
            self.nav.addItem(item)

        body.addWidget(self.stack)
        body.setStretchFactor(0, 0)   # 导航栏不伸缩
        body.setStretchFactor(1, 1)   # 页面栈占满剩余空间
        # 关键：splitter 第 0 段宽度必须与 nav_container 的 setFixedWidth 一致，
        # 否则差值会变成侧边栏与页面之间的「死空隙」（页面看起来悬浮、不贴边）。
        nav_w = self._nav_width_collapsed if self._nav_collapsed else self._nav_width_expanded
        body.setSizes([nav_w, 9800])
        # 禁用导航栏的拖拽分隔条：拖拽会拽出空隙，且宽度已由 _toggle_nav 固定控制
        body.setHandleWidth(1)
        h0 = body.handle(1)
        if h0 is not None:
            h0.setEnabled(False)
        root.addWidget(body, 1)

        self.setCentralWidget(central)
        self.nav.setCurrentRow(0)

        # 连线页面信号 -> MainWindow
        self._wire_pages()
        # 应用主题到各页面
        self._apply_theme_to_pages()
        # 注意：插件加载延后到 __init__ 末尾（_build_statusbar 之后），
        # 因为 PluginContext 需要 lbl_status，而它在 _build_statusbar 里创建。

    def _wire_pages(self) -> None:
        # 检测页：类别筛选变化
        self.page_detection.classes_changed.connect(self._on_classes_changed)
        # 检测页：视频内联播放控件（与顶部 ControlBar 等效）
        self.page_detection.video_play_toggled.connect(self._on_play_toggled)
        self.page_detection.video_seek_requested.connect(self._on_seek_requested)

        # ROI 页：用户操作
        self.page_roi.roi_created.connect(self._on_roi_created)
        self.page_roi.clear_roi_requested.connect(self._clear_roi)
        self.page_roi.import_requested.connect(self._import_roi)
        self.page_roi.export_requested.connect(self._export_roi)
        # ROI 页：视频内联播放控件（与检测页共享同一 worker）
        self.page_roi.video_play_toggled.connect(self._on_play_toggled)
        self.page_roi.video_seek_requested.connect(self._on_seek_requested)

        # 历史页：注入数据源
        self.page_history.set_history_manager(self._history)

        # 设置页：应用
        self.page_settings.settings_applied.connect(self._on_settings_applied)

    def _apply_theme_to_pages(self) -> None:
        for page in (self.page_detection, self.page_roi, self.page_stats, self.page_history, self.page_settings):
            page.set_palette(self._palette)
        self._refresh_nav_icons()
        # 主题变化时也通知已加载的插件（运行期切主题）
        self._dispatch_plugin_event("on_theme_changed", self._palette)

    # ====================================================================
    # 插件系统
    # ====================================================================
    def _make_plugin_context(self, plugin_name: str, plugin_dir: str) -> PluginContext:
        """为插件构造 PluginContext（聚合主程序提供的能力）。"""
        return PluginContext(
            project_root=self._project_root,
            plugin_dir=plugin_dir,
            config=self._cfg,
            palette=self._palette,
            detector=self._detector,
            roi_manager=self._roi_manager,
            stats=self._stats,
            history=self._history,
            alarm=self._alarm,
            logger=logger,
            get_last_frame=lambda: self._last_frame,
            show_status=self.lbl_status.setText,
        )

    def _load_plugins(self) -> None:
        """发现并加载 plugins/ 下的所有插件，把页面加进导航 + 页面栈。"""
        plugins_root = self._abs_path("plugins")
        self._plugin_mgr = PluginManager(plugins_root)
        loaded = self._plugin_mgr.load_all(self._make_plugin_context)
        for lp in loaded:
            self._register_plugin(lp)

    def _register_plugin(self, lp) -> None:
        """把单个已加载插件的 widget 加进 stack + nav，并连入事件分发。"""
        plugin = lp.plugin
        widget = lp.widget
        # 把插件页加进页面栈
        self.stack.addWidget(widget)
        # 主题应用到插件页（如果它有 set_palette 方法；Plugin 基类不强制）
        if hasattr(widget, "set_palette"):
            try:
                widget.set_palette(self._palette)
            except Exception:
                pass
        # 导航项：构造 icon + 标题，按 nav_after 决定插入位置
        nav_item = QListWidgetItem("" if self._nav_collapsed else plugin.title)
        nav_item.setData(Qt.UserRole, plugin)              # 存 Plugin 引用（图标用自定义逻辑）
        nav_item.setData(Qt.UserRole + 1, plugin.title)    # 标题（折叠/展开切换用）
        nav_item.setIcon(self._plugin_nav_icon(plugin, selected=False))
        # nav_after 定位：所有插件都追加到 5 个内置页之后（内置页固定占 nav 前 5 行，
        # _on_nav_changed 依赖「内置页行号 == stack 索引」这一不变量）。插件之间按
        # 加载顺序追加。nav_after 保留为元信息但不改变位置，避免破坏内置页行号映射。
        insert_row = self.nav.count()  # 追加到末尾
        self.nav.insertItem(insert_row, nav_item)
        # 注意：插件页通过 UserRole+2 关联 widget；_on_nav_changed 据此用 setCurrentWidget
        # 切换，而内置页用行号映射 stack 索引（前 5 行）。两者互不干扰。
        nav_item.setData(Qt.UserRole + 2, widget)
        self._plugin_pages.append(lp)
        # 刷新插件页主题
        try:
            plugin.on_theme_changed(self._palette)
        except Exception:
            logger.exception("插件 on_theme_changed 失败: %s", plugin.name)
        # 通知插件已就绪
        try:
            plugin.on_load()
        except Exception:
            logger.exception("插件 on_load 失败: %s", plugin.name)
        logger.info("插件已注册到导航: %s", plugin.title)

    def _plugin_nav_icon(self, plugin, selected: bool) -> QIcon:
        """加载插件导航图标。优先级：
        1. 绝对路径的 svg 文件
        2. 相对插件目录的 svg 文件（plugin.icon 文件名，如 "icon.svg"）
        3. assets/icons 下的内置图标名（如 "settings"）
        都没有则返回空 QIcon（显示文字）。"""
        from PyQt5.QtGui import QIcon
        from app.ui.widgets.svg_icon import _read_svg, _render_pixmap
        color = "#FFFFFF" if selected else self._palette.fg_sub
        size = getattr(self, "_nav_icon_px", 28)
        icon = (plugin.icon or "").strip()
        if not icon:
            return QIcon()
        # 1. 绝对路径
        if os.path.isabs(icon) and os.path.isfile(icon):
            path = icon
        # 2. 相对插件目录的文件（plugin.icon 是文件名，如 "icon.svg"）
        elif os.path.isfile(os.path.join(plugin.ctx.plugin_dir, icon)):
            path = os.path.join(plugin.ctx.plugin_dir, icon)
        # 3. assets/icons 下的内置图标名
        else:
            builtin = _read_svg(icon)
            if builtin is not None:
                return QIcon(_render_pixmap(builtin, color, size))
            return QIcon()
        try:
            with open(path, "r", encoding="utf-8") as f:
                svg_text = f.read()
            return QIcon(_render_pixmap(svg_text, color, size))
        except OSError:
            return QIcon()

    def _dispatch_plugin_event(self, method_name: str, *args) -> None:
        """安全地把一个事件回调分发给所有已加载插件（单个失败不影响其它）。"""
        for lp in self._plugin_pages:
            try:
                getattr(lp.plugin, method_name)(*args)
            except Exception:
                logger.exception("插件 %s.%s 失败", lp.plugin.name, method_name)

    def _toggle_nav(self) -> None:
        """折叠/展开侧边栏。折叠态仅显示图标，展开态显示图标+文字。"""
        self._nav_collapsed = not self._nav_collapsed
        collapsed = self._nav_collapsed
        # 容器宽度
        nav_w = self._nav_width_collapsed if collapsed else self._nav_width_expanded
        self._nav_container.setFixedWidth(nav_w)
        # 同步 splitter 第 0 段，避免宽度变化后留出死空隙
        parent = self._nav_container.parent()
        if parent is not None and hasattr(parent, "setSizes"):
            parent.setSizes([nav_w, 9800])
        # 切换按钮箭头方向
        self.btn_toggle_nav.setText("»" if collapsed else "«")
        # 每个项：折叠时清空文字（仅留图标），展开时恢复标题
        for i in range(self.nav.count()):
            item = self.nav.item(i)
            title = item.data(Qt.UserRole + 1)
            item.setText("" if collapsed else title)

    def _refresh_nav_icons(self) -> None:
        """刷新所有导航项图标（默认色 + 选中色）。
        内置页用 UserRole 存图标名；插件页用 UserRole 存 Plugin 引用。"""
        current = self.nav.currentRow()
        for i in range(self.nav.count()):
            item = self.nav.item(i)
            data = item.data(Qt.UserRole)
            widget = item.data(Qt.UserRole + 2)
            if widget is not None and isinstance(data, Plugin):  # 注：Plugin 已在文件头导入
                # 插件页
                item.setIcon(self._plugin_nav_icon(data, selected=(i == current)))
            elif isinstance(data, str):
                # 内置页（图标名）
                item.setIcon(self._nav_icon(data, selected=(i == current)))

    def _nav_icon(self, name: str, selected: bool) -> QIcon:
        """从 assets/icons/<name>.svg 加载图标，按主题着色。

        selected=True 用白色（与蓝色选中背景对比），否则用次文字色（默认态）。
        注意：选中项背景已是主题色（self._palette.primary），图标不能用同色，
        否则蓝图标画在蓝背景上会"隐形"——此处用 #FFFFFF 保证可见。
        """
        from app.ui.widgets.svg_icon import load_svg_icon
        color = "#FFFFFF" if selected else self._palette.fg_sub
        size = getattr(self, "_nav_icon_px", 28)
        return load_svg_icon(name, color, size)

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setProperty("role", "sub")
        self.lbl_source = QLabel("源: 无")
        self.lbl_source.setProperty("role", "sub")
        # 报警提示（底部内联，替代弹窗）
        self.lbl_alarm_msg = QLabel("")
        self.lbl_alarm_msg.setStyleSheet(
            f"color: {self._palette.alarm}; font-weight: bold; padding: 0 6px;"
        )
        self.lbl_alarm_msg.setVisible(False)
        self.lbl_clock = QLabel("")
        self.lbl_clock.setProperty("role", "sub")
        sb.addWidget(self.lbl_status, 2)
        sb.addWidget(self.lbl_source, 2)
        sb.addWidget(self.lbl_alarm_msg, 3)
        sb.addPermanentWidget(self.lbl_clock)
        # 报警提示自动清除定时器（单次触发）
        self._alarm_msg_timer = QTimer(self)
        self._alarm_msg_timer.setSingleShot(True)
        self._alarm_msg_timer.setInterval(4000)
        self._alarm_msg_timer.timeout.connect(self._clear_alarm_msg)
        self._tick_clock()

    def _show_alarm_msg(self, text: str) -> None:
        """底部状态栏内联显示报警提示，4 秒后自动清除。

        若状态栏被折叠，报警时自动展开，让提示可见。
        """
        sb = self.statusBar()
        if not sb.isVisible():
            sb.setVisible(True)
            self.act_toggle_statusbar.setChecked(True)
        self.lbl_alarm_msg.setText(f"● 报警：{text}")
        self.lbl_alarm_msg.setVisible(True)
        self._alarm_msg_timer.start()

    def _clear_alarm_msg(self) -> None:
        self.lbl_alarm_msg.setText("")
        self.lbl_alarm_msg.setVisible(False)

    def _toggle_statusbar(self) -> None:
        """显示/隐藏底部状态栏。"""
        sb = self.statusBar()
        visible = not sb.isVisible()
        sb.setVisible(visible)
        self.act_toggle_statusbar.setChecked(visible)
        self.lbl_status.setText("就绪" if visible else "就绪 (状态栏已隐藏)")

    def _apply_statusbar_state(self) -> None:
        """根据配置初始状态设置状态栏显隐与菜单勾选。默认显示。"""
        visible = bool(self._cfg.get("appearance.statusbar_visible", True))
        sb = self.statusBar()
        sb.setVisible(visible)
        self.act_toggle_statusbar.setChecked(visible)

    # ---- 导航 ----
    def _goto_page(self, idx: int) -> None:
        self.nav.setCurrentRow(idx)

    def _on_nav_changed(self, row: int) -> None:
        if not (0 <= row < self.nav.count()):
            return
        item = self.nav.item(row)
        # 优先用 item 关联的 widget（插件页）；内置页无关联，按行号映射
        widget = item.data(Qt.UserRole + 2)
        if widget is not None:
            self.stack.setCurrentWidget(widget)
        else:
            # 内置页：前 5 行对应 stack 索引 0..4（与加载顺序一致）
            self.stack.setCurrentIndex(row)
        self._refresh_nav_icons()
        # 切到 ROI 页时补一帧最近缓存，保证画布不是空白
        if self.stack.currentWidget() is self.page_roi and self._last_frame is not None:
            annotated, violator_indices, centers = self._last_frame
            rois = [r.points for r in self._roi_manager.regions]
            self.page_roi.update_frame(annotated, violator_indices, centers)
            self.page_roi.set_rois(rois)

    # ====================================================================
    # 检测器懒加载
    # ====================================================================
    def _lazy_init_detector(self) -> None:
        if self._detector is not None:
            return
        model_rel = self._cfg.get("detection.model_path", "assets/models/yolo26s-seg.pt")
        model_path = self._abs_path(model_rel)
        try:
            self.lbl_status.setText("正在加载模型…")
            device = self._resolve_device(self._cfg.get("detection.device", "cpu"))
            self._detector = Detector(
                model_path=model_path,
                device=device,
                conf=self._cfg.get("detection.conf", 0.45),
                iou=self._cfg.get("detection.iou", 0.5),
                classes=self._cfg.get("detection.classes", [0]),
                imgsz=self._cfg.get("detection.imgsz", 640),
            )
            self._stats.set_names(self._detector.names)
            self._build_alarm_engine()
            self.lbl_status.setText("就绪")
            logger.info("检测器初始化成功: %s", model_path)
        except FileNotFoundError:
            self._detector = None
            self.lbl_status.setText(f"模型未找到: {model_rel}")
            QMessageBox.warning(self, "模型缺失", f"模型文件不存在：\n{model_path}\n\n请在设置页指定模型路径。")
        except Exception as e:
            self._detector = None
            logger.exception("检测器初始化失败")
            self.lbl_status.setText(f"模型加载失败: {e}")
            QMessageBox.critical(self, "初始化失败", f"模型加载失败：\n{e}")

    @staticmethod
    def _resolve_device(configured: str) -> str:
        """根据 torch 实际能力解析最终 device。

        配置了 cuda 但当前 torch 是 CPU-only / 无可用 GPU 时，回退 cpu，
        避免 RuntimeError 让程序无法启动。每次启动都打印一次实际设备，
        防止「以为用了 GPU 其实没有」。
        """
        import torch
        want_cuda = "cuda" in str(configured).lower()
        if want_cuda:
            if torch.cuda.is_available():
                logger.info("设备: %s (GPU=%s)", configured, torch.cuda.get_device_name(0))
                return configured
            logger.warning(
                "配置要求 device=%s 但 torch.cuda.is_available()=False"
                "（可能是 CPU-only 版 torch），回退到 cpu。", configured,
            )
            return "cpu"
        logger.info("设备: cpu")
        return "cpu"

    def _build_alarm_engine(self) -> None:
        cfg = self._cfg
        alarm_cfg = AlarmConfig(
            enabled_visual=cfg.get("alarm.enabled_visual", True),
            enabled_sound=cfg.get("alarm.enabled_sound", True),
            enabled_snapshot=cfg.get("alarm.enabled_snapshot", True),
            enabled_log=cfg.get("alarm.enabled_log", True),
            sound_file=self._abs_path(cfg.get("alarm.sound_file", "")),
            cooldown_seconds=cfg.get("alarm.cooldown_seconds", 3.0),
            clip_pre_seconds=cfg.get("alarm.clip_pre_seconds", 2.0),
            clip_post_seconds=cfg.get("alarm.clip_post_seconds", 2.0),
            popup=cfg.get("alarm.popup", True),
            snapshots_dir=self._abs_path(cfg.get("paths.snapshots_dir", "data/snapshots")),
            logs_dir=self._abs_path(cfg.get("paths.logs_dir", "data/logs")),
        )
        if self._alarm is None:
            self._alarm = AlarmEngine(alarm_cfg)
        else:
            self._alarm.update_config(alarm_cfg)
        self._alarm.set_visual_callback(self._on_alarm_visual)
        self._alarm.set_sound_callback(self._on_alarm_sound)
        self._alarm.set_snapshot_callback(self._on_alarm_snapshot)
        self._alarm.set_log_callback(self._on_alarm_log)

    # ====================================================================
    # 工具栏信号
    # ====================================================================
    def _on_source_type_changed(self, src_type: SourceType) -> None:
        self.lbl_source.setText(f"源: {src_type.value}")

    def _on_start(self, payload) -> None:
        source, src_type = payload
        if self._detector is None:
            QMessageBox.information(self, "未就绪", "模型尚未加载完成，请稍候。")
            self.control_bar.on_stopped()
            return
        if source is None:
            QMessageBox.information(self, "未选择源", "请先选择数据源。")
            self.control_bar.on_stopped()
            return

        self._sync_detector_params()
        self._build_alarm_engine()
        self._stop_worker()
        # 重置保存状态：新一次识别开始，清除上次的录制结果
        self._cur_record = None
        self.control_bar.set_save_enabled(False)
        # 视频内联播放控件：仅视频文件源显示（先显示控件占位，范围由首个
        # progress_updated 信号到达时校正——此时采集线程已打开源、frame_count 可读）
        is_video = src_type == SourceType.VIDEO
        self.page_detection.set_video_mode(is_video)
        self.page_roi.set_video_mode(is_video)
        # 记录当前源信息（供保存时写历史元数据）
        self._cur_source_type = src_type
        if src_type == SourceType.IMAGE:
            self._cur_source_name = os.path.basename(str(source))
        elif src_type == SourceType.VIDEO:
            self._cur_source_name = os.path.basename(str(source))
        else:
            self._cur_source_name = f"摄像头{source}"
        try:
            if src_type == SourceType.IMAGE:
                from app.workers.image_worker import ImageWorker
                self._worker = ImageWorker(
                    str(source), self._detector, self._roi_manager,
                    self._stats, self._alarm, parent=self,
                )
            else:
                from app.workers.video_worker import VideoWorker
                # 视频文件源：自动录制到临时文件，识别完即可保存回放
                record_path = None
                if src_type == SourceType.VIDEO:
                    record_path = self._history.new_temp_video_path()
                self._worker = VideoWorker(
                    source, self._detector, self._roi_manager,
                    self._stats, self._alarm, record_path=record_path, parent=self,
                )
        except Exception as e:
            logger.exception("worker 创建失败")
            self.lbl_status.setText(f"启动失败: {e}")
            self.control_bar.on_stopped()
            return

        self._wire_worker(self._worker)
        self.lbl_status.setText("检测中")
        self.lbl_source.setText(f"源: {source if not isinstance(source, int) else f'camera{source}'}")
        self._worker.start()

    def _wire_worker(self, worker) -> None:
        # 同一帧广播给检测页与 ROI 页两个画布
        worker.frame_ready.connect(self._on_frame)
        worker.stats_ready.connect(self._on_stats)
        worker.alarm_ready.connect(self._on_alarm_ready)
        worker.error_occurred.connect(self._on_error)
        worker.finished_source.connect(self._on_finished)
        if hasattr(worker, "fps_updated"):
            worker.fps_updated.connect(self._on_fps)
        if hasattr(worker, "video_recorded"):
            worker.video_recorded.connect(self._on_video_recorded)
        if hasattr(worker, "progress_updated"):
            worker.progress_updated.connect(self._on_progress)

    def _sync_detector_params(self) -> None:
        if self._detector is None:
            return
        self._detector.set_conf(self._cfg.get("detection.conf", 0.45))
        self._detector.set_iou(self._cfg.get("detection.iou", 0.5))
        self._detector.set_classes(self._cfg.get("detection.classes", [0]) or None)
        try:
            self._detector.set_device(self._cfg.get("detection.device", "cpu"))
        except Exception:
            pass

    def _on_pause(self) -> None:
        if self._worker and hasattr(self._worker, "pause"):
            self._worker.pause()
            self.lbl_status.setText("已暂停")
        # 内联播放控件同步为「暂停」态
        self.page_detection.set_video_playing(False)
        self.page_roi.set_video_playing(False)

    def _on_resume(self) -> None:
        if self._worker and hasattr(self._worker, "resume"):
            self._worker.resume()
            self.lbl_status.setText("检测中")
        # 内联播放控件同步为「播放」态
        self.page_detection.set_video_playing(True)
        self.page_roi.set_video_playing(True)

    def _on_play_toggled(self) -> None:
        """检测页/ROI 页内联播放按钮被点击：切换 worker 暂停/继续。
        与顶部 ControlBar 的暂停/继续等效，二者状态由各自按钮分别表达。"""
        if not (self._worker and hasattr(self._worker, "_pause_flag")):
            return
        if getattr(self._worker, "_pause_flag", False):
            self._on_resume()
        else:
            self._on_pause()

    def _on_seek_requested(self, frame_idx: int) -> None:
        """检测页/ROI 页进度条拖动：跳转 worker 到指定帧。"""
        if self._worker and hasattr(self._worker, "seek"):
            self._worker.seek(frame_idx)

    def _on_progress(self, cur: int, total: int) -> None:
        """worker 推进：更新可见页的进度条位置。
        首次到达时 total 已知，补校正进度条范围（_on_start 时还读不到 frame_count）。"""
        # 范围初始化：start 时是 (0,0)，首帧 progress 到达后校正一次
        if total > 0:
            fps = getattr(self._worker, "source_fps", 0.0) if self._worker else 0.0
            for page in (self.page_detection, self.page_roi):
                bar = page.playback_bar
                if bar.isVisible() and bar.frame_count != total:
                    bar.set_range(total, fps)
        # 只更新可见页的位置（隐藏页不刷，避免无谓重绘）
        current = self.stack.currentWidget()
        if current in (self.page_detection, self.page_roi):
            current.set_video_position(cur)

    def _on_stop(self) -> None:
        self._stop_worker()
        self.control_bar.on_stopped()
        self.lbl_status.setText("已停止")
        # 停止后隐藏内联播放控件（下次开始时会按源类型重新显示）
        self.page_detection.set_video_mode(False)
        self.page_roi.set_video_mode(False)

    def _stop_worker(self) -> None:
        if self._worker is not None:
            try:
                if hasattr(self._worker, "stop"):
                    self._worker.stop()
                self._worker.wait(2000)
            except Exception:
                pass
            self._worker = None
        # 停止时把统计缓冲区的剩余采样落盘，避免丢失最近几秒数据
        try:
            self._stats.flush()
        except Exception:
            pass

    # ====================================================================
    # Worker 信号 -> 路由到页面
    # ====================================================================
    def _on_frame(self, annotated: np.ndarray, violator_indices, centers) -> None:
        rois = [r.points for r in self._roi_manager.regions]
        # 缓存最近一帧（切到 ROI 页时补帧用）
        self._last_frame = (annotated, violator_indices, centers)
        # 只给当前可见页喂帧：隐藏页的 VideoCanvas 不做昂贵的 np→QImage→QPixmap 转换，
        # 避免同一帧被渲染两次。切页时由 _on_nav_changed 补帧。
        current = self.stack.currentWidget()
        if current is self.page_detection:
            self.page_detection.update_frame(annotated, violator_indices, centers)
            self.page_detection.set_rois(rois)
        elif current is self.page_roi:
            self.page_roi.update_frame(annotated, violator_indices, centers)
            self.page_roi.set_rois(rois)
        # 状态读数
        try:
            n_objs = len(centers) if centers is not None else 0
        except TypeError:
            n_objs = 0
        self._cur_objs = n_objs
        self._cur_alarm = bool(violator_indices)
        # 状态文字节流：只标记脏，由 _stats_timer(1Hz) 统一刷新，避免每帧 setText
        self._status_dirty = True
        # 插件：仅当当前可见页是某个插件页时，分发帧（隐藏插件不收，避免浪费 CPU）
        lp = self._current_plugin_page()
        if lp is not None:
            try:
                lp.plugin.on_frame(annotated, violator_indices, centers)
            except Exception:
                logger.exception("插件 on_frame 失败: %s", lp.plugin.name)

    def _current_plugin_page(self):
        """若当前可见页是插件页，返回对应的 _LoadedPlugin；否则 None。"""
        cur = self.stack.currentWidget()
        for lp in self._plugin_pages:
            if lp.widget is cur:
                return lp
        return None

    def _on_stats(self, data: dict) -> None:
        # 节流：每帧到达只标记脏数据，真正的全量查询交给 1Hz 的 _stats_timer。
        # 避免 _samples 无界增长时每帧遍历几万条采样拖垮主线程。
        self._stats_dirty = True

    def _refresh_periodic(self) -> None:
        """1Hz 周期刷新：统计图表 + 状态文字。把每帧的昂贵工作收敛到这里。"""
        if self._stats_dirty:
            self._stats_dirty = False
            p = self.page_stats
            p.update_class_counts(self._stats.class_counts())
            p.update_class_ratio(self._stats.class_ratio())
            labels, values = self._stats.time_series(bin_seconds=30)
            p.update_time_series(labels, values)
            a_labels, a_values = self._stats.alarm_trend(bin_seconds=30)
            p.update_alarm_trend(a_labels, a_values)
            p.update_alarm_total(self._stats.alarm_total)
            p.mark_stats_dirty()
        if self._status_dirty:
            self._status_dirty = False
            self.page_detection.update_status(
                self._cur_fps, self._cur_objs, self._cur_alarm,
            )

    def _on_alarm_ready(self, roi_id: int, cls_ids: list, confs: list) -> None:
        self._cur_alarm = True
        if not self._alarm_timer.isActive():
            self._alarm_timer.start()

    def _on_fps(self, fps: float) -> None:
        self._cur_fps = fps
        # 不每帧刷新状态文字（人眼无法分辨 30Hz 文字刷新，setText 会触发 layout+repaint）。
        # 实际刷新交给 _stats_timer（1Hz）里的 _refresh_status()。
        self._status_dirty = True

    def _on_error(self, msg: str) -> None:
        logger.error("worker 错误: %s", msg)
        self.lbl_status.setText(f"错误: {msg}")
        self.control_bar.on_stopped()

    def _on_finished(self) -> None:
        self.lbl_status.setText("完成")
        self.control_bar.on_stopped()
        self._cur_alarm = False
        self.page_detection.update_status(self._cur_fps, self._cur_objs, False)
        # 识别完成：若有可保存结果（图片帧 / 摄像头帧 / 视频录制），启用保存按钮
        if self._last_frame is not None:
            self.control_bar.set_save_enabled(True)

    def _on_video_recorded(self, tmp_path: str, duration: float) -> None:
        """VideoWorker 录制完成（视频文件源自然播完）。保存待归档。"""
        self._cur_record = (tmp_path, duration, self._cur_source_name)
        self.control_bar.set_save_enabled(True)
        logger.info("视频录制就绪，可保存: %.1fs", duration)

    def _on_save(self) -> None:
        """保存当前识别结果到历史记录。"""
        if self._cur_source_type == SourceType.VIDEO and self._cur_record is not None:
            # 视频文件源：归档录制的临时视频
            tmp_path, duration, source_name = self._cur_record
            counts, class_names = self._collect_class_stats()
            record = self._history.add_video(
                tmp_path, source_name,
                counts=counts, class_names=class_names,
                alarms=self._stats.alarm_total, duration=duration,
            )
            if record is not None:
                self._cur_record = None  # 已归档，避免重复保存
                self.control_bar.set_save_enabled(False)
                self.lbl_status.setText("已保存到历史记录")
                self.page_history.refresh()
                QMessageBox.information(self, "已保存", f"视频已保存到历史记录\n时长 {duration:.1f}s")
            else:
                QMessageBox.warning(self, "保存失败", "视频录制文件无效，无法保存。")
        elif self._last_frame is not None:
            # 图片 / 摄像头：保存当前帧
            annotated, _violators, _centers = self._last_frame
            counts, class_names = self._collect_class_stats()
            self._history.add_image(
                annotated, self._cur_source_name or "未命名",
                counts=counts, class_names=class_names,
                alarms=self._stats.alarm_total,
            )
            self.lbl_status.setText("已保存到历史记录")
            self.page_history.refresh()
            # 图片保存后保留按钮（可重复保存不同帧？这里禁用，避免误存同一帧）
            self.control_bar.set_save_enabled(False)
            QMessageBox.information(self, "已保存", "当前画面已保存到历史记录")
        else:
            QMessageBox.information(self, "无可保存", "请先进行一次识别。")

    def _collect_class_stats(self) -> tuple[dict[int, int], dict[int, str]]:
        """收集当前累计的各类计数与类名（供历史元数据）。"""
        try:
            counts_raw = self._stats.class_counts()  # {类名: 数}
            names = self._detector.names if self._detector else {}
            # 反查 cls_id
            counts: dict[int, int] = {}
            for cid, cname in names.items():
                if cname in counts_raw:
                    counts[int(cid)] = counts_raw[cname]
            return counts, {int(k): v for k, v in names.items()}
        except Exception:
            return {}, {}

    # ====================================================================
    # 报警四通道
    # ====================================================================
    def _on_alarm_visual(self, event: AlarmEvent) -> None:
        self._visual_alarm.emit(event)

    def _on_alarm_sound(self, event: AlarmEvent) -> None:
        """声音回调：在工作线程被 AlarmEngine.trigger 同步调用。
        QSound 是 QObject，不能在工作线程创建/播放（会触发跨线程
        "Cannot create children for a parent that is in a different thread"）。
        改为发信号 _sound_alarm，转发到主线程执行。
        """
        sound_file = self._alarm.sound_file if self._alarm else ""
        if sound_file and os.path.isfile(sound_file):
            self._sound_alarm.emit(sound_file)

    def _play_alarm_sound(self, sound_file: str) -> None:
        """主线程槽：实际播放报警声音。"""
        try:
            QSound.play(sound_file)
        except Exception:
            pass

    def _on_alarm_snapshot(self, event: AlarmEvent) -> None:
        import cv2
        d = self._ensure_dir(self._cfg.get("paths.snapshots_dir", "data/snapshots"))
        ts = datetime.fromtimestamp(event.timestamp).strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(d, f"alarm_roi{event.roi_id}_{ts}.jpg")
        try:
            ok, buf = cv2.imencode(".jpg", event.frame)
            if ok:
                buf.tofile(path)
        except Exception:
            logger.exception("截图写盘失败")

    def _on_alarm_log(self, event: AlarmEvent) -> None:
        d = self._ensure_dir(self._cfg.get("paths.logs_dir", "data/logs"))
        path = os.path.join(d, "alarms.csv")
        try:
            new_file = not os.path.isfile(path)
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(["timestamp", "datetime", "roi_id", "class_ids", "confs"])
                names = [self._classes_meta.get(str(c), {}).get("en", str(c)) for c in event.cls_ids]
                w.writerow([
                    f"{event.timestamp:.3f}",
                    datetime.fromtimestamp(event.timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                    event.roi_id,
                    ",".join(names),
                    ",".join(f"{c:.2f}" for c in event.confs),
                ])
        except OSError:
            logger.exception("报警日志写盘失败")

    def _handle_visual_alarm(self, event: AlarmEvent) -> None:
        names = [self._classes_meta.get(str(c), {}).get("en", str(c)) for c in event.cls_ids]
        item = AnomalyItem(
            timestamp=event.timestamp,
            frame=event.frame,
            cls_names=names,
            confs=list(event.confs),
            roi_id=event.roi_id,
        )
        self.page_stats.append_anomaly(item)
        self._cur_alarm = True
        if not self._alarm_timer.isActive():
            self._alarm_timer.start()
        # 报警提示：底部状态栏内联显示（不弹窗）
        text = ", ".join(names) or "目标"
        self._show_alarm_msg(f"检测到 {text} 进入 ROI{event.roi_id}")
        # 插件：分发报警事件
        self._dispatch_plugin_event("on_alarm", event)

    # ====================================================================
    # ROI 操作
    # ====================================================================
    def _on_roi_created(self, frame_pts: list) -> None:
        region = self._roi_manager.add([tuple(p) for p in frame_pts])
        self._refresh_roi_views()
        self.lbl_status.setText(f"已添加 {region.label}")

    def _clear_roi(self) -> None:
        self._roi_manager.clear()
        self._refresh_roi_views()
        self.lbl_status.setText("已清除所有 ROI")

    def _refresh_roi_views(self) -> None:
        rois = [r.points for r in self._roi_manager.regions]
        self.page_detection.set_rois(rois)
        self.page_roi.set_rois(rois)
        self.page_roi.refresh_roi_list(self._roi_manager.regions)

    def _import_roi(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入 ROI", "", "JSON (*.json)")
        if not path:
            return
        import json
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "rois" in data:
                data = data["rois"]
            self._roi_manager.from_list(data)
            self._refresh_roi_views()
            self.lbl_status.setText(f"已导入 {len(self._roi_manager)} 个 ROI")
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))

    def _export_roi(self) -> None:
        if len(self._roi_manager) == 0:
            QMessageBox.information(self, "无 ROI", "当前没有 ROI 可导出。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 ROI", "rois.json", "JSON (*.json)")
        if not path:
            return
        import json
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"rois": self._roi_manager.to_list()}, f, ensure_ascii=False, indent=2)
            self.lbl_status.setText(f"已导出 {len(self._roi_manager)} 个 ROI")
        except OSError as e:
            QMessageBox.warning(self, "导出失败", str(e))

    # ====================================================================
    # 类别筛选
    # ====================================================================
    def _on_classes_changed(self, ids: list) -> None:
        ids = [int(i) for i in ids]
        self._cfg.set("detection.classes", ids, autosave=True)
        if self._detector is not None:
            self._detector.set_classes(ids or None)

    # ====================================================================
    # 状态灯 / 时钟
    # ====================================================================
    def _blink_alarm(self) -> None:
        self._alarm_glow = not self._alarm_glow
        self.page_detection.update_status(self._cur_fps, self._cur_objs, self._alarm_glow)

    def _tick_clock(self) -> None:
        self.lbl_clock.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # ====================================================================
    # 设置应用
    # ====================================================================
    def _on_settings_applied(self) -> None:
        new_theme = self._cfg.get("appearance.theme", "dark")
        if new_theme != self._theme:
            self._theme = new_theme
            self._palette = get_palette(self._theme)
            app = self._qapp()
            if app is not None:
                apply_theme(app, self._theme, self._cfg.get("appearance.font_size", 13))
            self._apply_theme_to_pages()
            # 切换主题后刷新标题栏颜色
            set_titlebar_color(self, self._palette)
            # 刷新底部工具条样式，跟随主题
            self._repolish_toolbar()

        if self._detector is not None:
            self._sync_detector_params()
            self._build_alarm_engine()
        self.lbl_status.setText("设置已应用")

    def _qapp(self):
        from PyQt5.QtWidgets import QApplication
        return QApplication.instance()

    # ====================================================================
    # 配置变更监听
    # ====================================================================
    def _on_config_changed(self, dotted_key: str) -> None:
        if dotted_key.startswith("detection.") and self._detector is not None:
            self._sync_detector_params()

    # ====================================================================
    # 显示事件：窗口首次显示后才有有效 HWND，此时给原生标题栏上主题色
    # ====================================================================
    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 仅在首次显示时上色一次（避免每次重绘都调 DWM API）
        if not getattr(self, "_titlebar_themed", False):
            self._titlebar_themed = set_titlebar_color(self, self._palette)
        # 首次显示时强制刷新底部工具条样式：QToolBar 在 Windows 上有时保留
        # 原生背景（白色），需 unpolish/polish 让 QSS 重新生效。
        if not getattr(self, "_toolbar_repolished", False):
            self._toolbar_repolished = True
            self._repolish_toolbar()

    def _repolish_toolbar(self) -> None:
        """强制重新应用样式到底部工具条，确保跟随主题色（非白色）。"""
        tb = getattr(self, "_bottom_toolbar", None)
        if tb is None:
            return
        from PyQt5.QtWidgets import QApplication
        style = QApplication.instance().style()
        for w in [tb] + tb.findChildren(type(tb.widgetForAction(tb.actions()[0])) if tb.actions() else []):
            try:
                style.unpolish(w)
                style.polish(w)
            except Exception:
                pass

    # ====================================================================
    # 关闭
    # ====================================================================
    def closeEvent(self, event) -> None:
        self._stop_worker()
        # 插件：退出前释放资源
        self._dispatch_plugin_event("on_shutdown")
        if self._alarm is not None:
            try:
                self._alarm.shutdown()
            except Exception:
                pass
        super().closeEvent(event)
