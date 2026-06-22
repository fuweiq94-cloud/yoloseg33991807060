# 插件系统

在不改动源程序的前提下扩展功能。每个插件 = 一个独立的「模块」，自带页面，
启动后自动出现在左侧导航栏。

## 快速开始（30 秒写一个插件）

1. 在 `plugins/` 下新建一个目录，比如 `plugins/my_tool/`
2. 创建 `plugins/my_tool/plugin.py`：

   ```python
   from app.plugins.base import Plugin, PluginContext

   class MyTool(Plugin):
       name = "my_tool"        # 唯一标识
       title = "我的工具"        # 导航栏文字
       icon = ""               # 留空用默认图标

       def create_widget(self):
           from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
           w = QWidget()
           v = QVBoxLayout(w)
           v.addWidget(QLabel("你好，这是我的插件页面"))
           return w

   def create_plugin(ctx: PluginContext) -> "MyTool":
       return MyTool(ctx)
   ```

3. 启动程序（`python run.py`）——左侧导航自动多出一个图标，点开就是你的页面。

> 不用重启 IDE，不用改任何源代码。删掉目录就卸载。

## 目录结构

```
plugins/
├── README.md          ← 本文件
└── hello/             ← 自带示例插件（可直接删掉）
    ├── plugin.py      ← 插件入口（必须）
    └── icon.svg       ← 自带图标（可选）
```

## 插件能调用什么（PluginContext）

通过 `self.ctx` 访问主程序提供的能力：

| 能力 | 用法 | 说明 |
|------|------|------|
| 全局配置 | `ctx.config.get("detection.device")` | 读写 `config/settings.json`（点分路径） |
| YOLO 检测器 | `ctx.detector` | 可能未加载完，**用前判空** |
| 统计 | `ctx.stats` | StatsCollector |
| 历史记录 | `ctx.history` | HistoryManager |
| 报警引擎 | `ctx.alarm` | AlarmEngine |
| ROI 管理 | `ctx.roi_manager` | RoiManager |
| 主题配色 | `ctx.palette` | 当前 Palette |
| 最近一帧 | `ctx.get_last_frame()` | `(annotated, violator_indices, centers)` 或 `None` |
| 状态栏 | `ctx.show_status("文字")` | 在底部状态栏显示一行 |
| 项目根 | `ctx.project_root` | 绝对路径 |
| 插件目录 | `ctx.plugin_dir` / `ctx.plugin_path("a.txt")` | 访问插件自带资源 |

## 订阅事件（按需覆盖基类方法）

```python
class MyTool(Plugin):
    def create_widget(self): ...           # 必需：返回页面 widget

    def on_load(self): ...                 # 可选：页面已加入导航后
    def on_theme_changed(self, palette):   # 可选：主题切换时刷新样式
    def on_frame(self, annotated, violator_indices, centers):  # 可选：每帧（仅本页可见时）
    def on_alarm(self, event): ...         # 可选：报警事件
    def on_shutdown(self): ...             # 可选：程序退出前释放资源
```

> `on_frame` **只在你的页面可见时**分发，隐藏时不浪费 CPU。

## 插件元信息（类属性）

```python
class MyTool(Plugin):
    name = "my_tool"      # 唯一标识（英文短名，日志/排错用）
    title = "我的工具"     # 导航栏文字
    icon = ""             # 图标：留空=默认；填 assets/icons 名字；或本目录 .svg 文件名
    nav_after = ""        # 保留字段，当前插件统一追加到导航末尾
```

图标三种填法：
- `icon = ""` → 默认方块
- `icon = "settings"` → 用内置 `assets/icons/settings.svg`
- `icon = "icon.svg"` → 用插件目录下的 `icon.svg`（自带图标，推荐）

## 加载机制

- 启动时扫描 `plugins/` 下所有含 `plugin.py` 的子目录，按字母序加载
- `plugin.py` 必须导出 `create_plugin(ctx)` 工厂函数（推荐）或模块级 `PLUGIN` 实例
- **单个插件加载失败不影响其它插件**：捕获异常 + 记日志 + 跳过
- 每个 `plugin.py` 作为独立模块加载，不污染全局命名空间

## 完整示例

见 `plugins/hello/plugin.py`：演示了读取配置、订阅帧、抓帧预览、状态栏、主题适配。
直接复制 `plugins/hello/` 改名即可开始。
