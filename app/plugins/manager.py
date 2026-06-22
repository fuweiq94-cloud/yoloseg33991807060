"""插件管理器：发现、加载、注册插件。

发现机制（约定优于配置，零配置）：
- 插件根目录：<项目根>/plugins/
- 每个插件 = 一个子目录，内含 plugin.py
- plugin.py 必须导出一个 Plugin 子类实例，二选一：
    (1) 模块级变量 PLUGIN: Plugin   —— 最简单
    (2) 工厂函数 create_plugin(ctx) -> Plugin  —— 需要构造参数时用
- 插件自带资源（如 icon.svg）放在自己的目录里，通过 ctx.plugin_path() 取。

加载流程：
  1. 扫描 plugins/ 下所有含 plugin.py 的子目录（按字母序，加载顺序确定）
  2. 用 importlib 把每个 plugin.py 作为独立模块导入
  3. 取 PLUGIN 实例 或 调用 create_plugin(ctx)
  4. 失败的插件不影响其它插件：捕获异常 + 记日志 + 跳过

插件之间相互独立：每个 plugin.py 以独立模块名加载，不会污染全局命名空间。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from typing import TYPE_CHECKING, Optional

from app.plugins.base import Plugin, PluginContext
from app.utils.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger()


class _LoadedPlugin:
    """已加载的插件 + 其页面 widget（供 MainWindow 管理）。"""
    def __init__(self, plugin: Plugin, widget) -> None:
        self.plugin = plugin
        self.widget = widget


class PluginManager:
    """发现并加载 plugins/ 下的所有插件。"""

    def __init__(self, plugins_root: str) -> None:
        # plugins_root：项目根下的 plugins 目录绝对路径
        self._root = plugins_root
        self._loaded: list[_LoadedPlugin] = []

    @property
    def loaded(self) -> "list[_LoadedPlugin]":
        return list(self._loaded)

    def discover(self) -> "list[str]":
        """返回所有含 plugin.py 的插件目录（绝对路径，字母序）。"""
        if not os.path.isdir(self._root):
            return []
        result = []
        for name in sorted(os.listdir(self._root)):
            d = os.path.join(self._root, name)
            if os.path.isdir(d) and os.path.isfile(os.path.join(d, "plugin.py")):
                result.append(d)
        return result

    def load_all(self, ctx_factory) -> "list[_LoadedPlugin]":
        """加载所有插件。

        ctx_factory(plugin_name, plugin_dir) -> PluginContext：由调用方提供，
        因为 PluginContext 需要主程序的服务，manager 不应知道这些细节。
        """
        for plugin_dir in self.discover():
            plugin_name = os.path.basename(plugin_dir)
            try:
                plugin = self._load_one(plugin_name, plugin_dir, ctx_factory)
                if plugin is None:
                    continue
                widget = plugin.create_widget()
                if widget is None:
                    raise ValueError("create_widget() 返回了 None")
                self._loaded.append(_LoadedPlugin(plugin, widget))
                logger.info("插件已加载: %s (%s)", plugin.name, plugin.title)
            except Exception as e:
                # 单个插件失败不阻断其它插件加载
                logger.exception("插件加载失败 [%s]: %s", plugin_name, e)
        return list(self._loaded)

    def _load_one(self, plugin_name: str, plugin_dir: str, ctx_factory) -> Optional[Plugin]:
        """把单个 plugin.py 作为独立模块导入，取出 Plugin 实例。

        用 importlib.util 显式指定模块名（plugins_<name>）和文件路径加载，
        不依赖 sys.path，避免插件目录里意外有同名标准库模块被优先导入。
        """
        module_name = f"plugins_{plugin_name}"
        # 若同名模块已加载（热重载场景），先移除旧引用
        sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(module_name, os.path.join(plugin_dir, "plugin.py"))
        if spec is None or spec.loader is None:
            logger.warning("无法为插件创建模块 spec: %s", plugin_name)
            return None
        module = importlib.util.module_from_spec(spec)
        # 注册到 sys.modules 让插件内部能用「from plugins_<name> import xxx」
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # 优先用工厂函数（需要 ctx 才能构造的场景）
        if hasattr(module, "create_plugin"):
            ctx = ctx_factory(plugin_name, plugin_dir)
            return module.create_plugin(ctx)
        # 否则取模块级 PLUGIN 实例，并把 ctx 注入进去（基类 __init__ 需要 ctx）
        if hasattr(module, "PLUGIN"):
            inst = module.PLUGIN
            ctx = ctx_factory(plugin_name, plugin_dir)
            inst.ctx = ctx
            return inst
        logger.warning("插件 %s 未导出 PLUGIN 或 create_plugin", plugin_name)
        return None
