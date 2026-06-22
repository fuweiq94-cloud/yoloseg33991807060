"""插件系统冲烟测试。

验证 PluginManager 的发现 / 加载 / 容错：
  1. discover()：正确识别含 plugin.py 的子目录，跳过无 plugin.py 的目录。
  2. load_all()：工厂函数 create_plugin(ctx) 被调用，PluginContext 正确注入，
     create_widget() 返回非 None。
  3. 容错：一个插件抛异常不影响其它插件加载。
  4. PluginContext：abs_path / plugin_path / get_last_frame / show_status 便捷方法。
"""
from __future__ import annotations

import os
import sys
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 注入 dummy PyQt5.QtWidgets（插件 create_widget 可能 import 它）
import types
sys.modules.setdefault("PyQt5", types.ModuleType("PyQt5"))
QtCore = types.ModuleType("PyQt5.QtCore")
class _Enum:
    AlignCenter = 0
sys.modules["PyQt5.QtCore"] = QtCore
QtWidgets = types.ModuleType("PyQt5.QtWidgets")
def _w(*a, **kw): return object()
QtWidgets.QWidget = _w
QtWidgets.QLabel = _w
QtWidgets.QVBoxLayout = _w
QtWidgets.QHBoxLayout = _w
QtWidgets.QPushButton = _w
QtWidgets.QFrame = _w
sys.modules["PyQt5.QtWidgets"] = QtWidgets

from app.plugins import PluginManager, PluginContext, Plugin  # noqa: E402


GOOD_PLUGIN = '''
from app.plugins.base import Plugin, PluginContext


class Good(Plugin):
    name = "good"
    title = "好插件"
    icon = ""

    def create_widget(self):
        return object()  # 非 None 即可

    def on_frame(self, a, b, c):
        self.frame_seen = True


def create_plugin(ctx):
    p = Good(ctx)
    p.ctx_injected_ok = ctx is not None
    return p
'''

BAD_PLUGIN = '''
raise RuntimeError("故意失败，验证容错")
'''


def _make_plugins_dir():
    d = tempfile.mkdtemp(prefix="plugins_test_")
    good = os.path.join(d, "good")
    bad = os.path.join(d, "bad")
    os.makedirs(good)
    os.makedirs(bad)
    with open(os.path.join(good, "plugin.py"), "w", encoding="utf-8") as f:
        f.write(GOOD_PLUGIN)
    with open(os.path.join(bad, "plugin.py"), "w", encoding="utf-8") as f:
        f.write(BAD_PLUGIN)
    # 一个不含 plugin.py 的目录，应被忽略
    os.makedirs(os.path.join(d, "notaplugin"))
    return d


def _make_ctx(plugin_dir):
    return PluginContext(
        project_root=ROOT,
        plugin_dir=plugin_dir,
        config=None,
        palette=None,
        get_last_frame=lambda: ("frame", [], None),
        show_status=lambda s: None,
    )


def test_discover():
    d = _make_plugins_dir()
    try:
        mgr = PluginManager(d)
        found = mgr.discover()
        names = sorted(os.path.basename(p) for p in found)
        print(f"[discover] 发现: {names}")
        assert names == ["bad", "good"], f"应发现 good 和 bad，实际 {names}"
        assert "notaplugin" not in names, "不含 plugin.py 的目录不应被发现"
        print("[OK] discover 通过")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_load_all_with_factory_and_tolerance():
    d = _make_plugins_dir()
    try:
        mgr = PluginManager(d)
        loaded = mgr.load_all(lambda name, pdir: _make_ctx(pdir))
        # bad 插件应被容错跳过，good 应成功
        assert len(loaded) == 1, f"应只加载 good（bad 容错跳过），实际 {len(loaded)}"
        lp = loaded[0]
        assert lp.plugin.name == "good"
        assert lp.widget is not None, "create_widget() 返回 None 应失败"
        # 工厂函数注入的 ctx 应到达插件
        assert getattr(lp.plugin, "ctx_injected_ok", False), "ctx 未注入到插件"
        # ctx 便捷方法（路径比较用 normpath 规整斜杠差异）
        ctx = lp.plugin.ctx
        assert os.path.normpath(ctx.abs_path("a/b")) == os.path.normpath(os.path.join(ROOT, "a", "b")), "abs_path 失败"
        assert os.path.normpath(ctx.plugin_path("x")) == os.path.normpath(os.path.join(ctx.plugin_dir, "x")), "plugin_path 失败"
        assert ctx.get_last_frame()[0] == "frame", "get_last_frame 失败"
        print("[OK] load_all（工厂注入 + 容错）通过")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_plugin_base_contract():
    """Plugin 基类的事件回调默认是 no-op（不抛异常），子类按需覆盖。"""
    ctx = PluginContext(project_root=ROOT, plugin_dir=ROOT, config=None, palette=None)
    p = Plugin(ctx)
    # 默认实现应可调用且不抛
    p.on_load()
    p.on_theme_changed(None)
    p.on_frame(None, [], None)
    p.on_alarm(None)
    p.on_shutdown()
    # create_widget 未覆盖应抛 NotImplementedError
    try:
        p.create_widget()
        assert False, "未覆盖 create_widget 应抛 NotImplementedError"
    except NotImplementedError:
        pass
    print("[OK] Plugin 基类契约通过")


if __name__ == "__main__":
    test_discover()
    test_load_all_with_factory_and_tolerance()
    test_plugin_base_contract()
    print("\n全部通过 ✅")
