"""插件系统：在不改动源程序的前提下扩展功能。

快速上手（写一个新插件）：
  1. 在项目根 plugins/ 下新建一个目录，比如 plugins/my_tool/
  2. 在里面创建 plugin.py，内容：

         from app.plugins.base import Plugin, PluginContext

         class MyTool(Plugin):
             name = "my_tool"
             title = "我的工具"
             icon = ""            # 留空用默认图标；或填 assets/icons 下的名字
             nav_after = ""       # 留空=追加到导航末尾

             def create_widget(self):
                 from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
                 w = QWidget()
                 v = QVBoxLayout(w)
                 v.addWidget(QLabel("你好，这是我的插件页面"))
                 return w

         # 关键：导出实例（插件管理器会取这个）
         PLUGIN = None  # 占位；下面用工厂函数动态创建

         def create_plugin(ctx: PluginContext) -> "MyTool":
             return MyTool(ctx)

  3. 启动程序——左侧导航会自动多出一个图标，点开就是你的页面。

插件能做什么（通过 ctx / 事件回调）：
  - ctx.config            读写全局配置（config/settings.json）
  - ctx.detector          YOLO 检测器（用前判空，模型可能还在加载）
  - ctx.stats / ctx.history / ctx.alarm / ctx.roi_manager  核心服务
  - ctx.palette           当前主题配色
  - ctx.get_last_frame()  拿最近一帧检测结果
  - ctx.show_status(text) 在状态栏显示一行文字
  - ctx.plugin_path(rel)  访问插件自带资源（自己目录下的文件）
  - 覆盖 on_theme_changed / on_frame / on_alarm / on_shutdown 订阅事件

详细说明见 plugins/README.md。
"""
from app.plugins.base import Plugin, PluginContext
from app.plugins.manager import PluginManager

__all__ = ["Plugin", "PluginContext", "PluginManager"]
