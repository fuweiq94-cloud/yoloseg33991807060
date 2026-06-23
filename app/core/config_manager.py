"""配置管理：读写 config/settings.json，深合并默认值，路径式访问。

无 Qt 依赖，纯逻辑。变更通知通过回调列表实现（core 层不直接发 Qt 信号）。
"""
from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Callable

# 默认配置，与 config/settings.json 保持一致；作为深合并基准，确保缺字段时有默认值。
DEFAULT_CONFIG: dict = {
    "detection": {
        "model_path": "assets/models/yolo26s-seg.pt",
        "device": "cpu",
        "conf": 0.45,
        "iou": 0.5,
        "classes": [0],
        "imgsz": 640,
        "show_masks": True,
        "show_boxes": True,
        "show_labels": True,
    },
    "alarm": {
        "enabled_visual": True,
        "enabled_sound": True,
        "enabled_snapshot": True,
        "enabled_log": True,
        "enabled_clip": True,
        "sound_file": "assets/sounds/alarm.wav",
        "cooldown_seconds": 3.0,
        "clip_pre_seconds": 2.0,
        "clip_post_seconds": 2.0,
        # 驻留判定：目标在 ROI 内连续停留 ≥ dwell_seconds 才报警。0 = 关闭（瞬时）。
        # dwell_grace = 离开宽限期（抗检测抖动）。
        "dwell_seconds": 0.0,
        "dwell_grace": 1.0,
        # 触发报警的类别白名单。null = 所有类别都报警（向后兼容）。
        # 与 detection.classes（检测类别）区分：这是控制"哪些类别进 ROI 才报警"。
        "classes": None,
        "popup": True,
    },
    "roi": {
        "line_color": "#2D6CDF",
        "fill_alpha": 60,
        "line_width": 2,
    },
    "appearance": {
        "theme": "dark",
        "font_size": 13,
        "primary_color": "#2D6CDF",
    },
    "paths": {
        "snapshots_dir": "data/snapshots",
        "clips_dir": "data/clips",
        "logs_dir": "data/logs",
        "history_dir": "data/history",
    },
    "source": {
        "last_type": "camera",
        "last_camera_index": 0,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并 override 到 base 的副本，override 优先。"""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class ConfigManager:
    """单例配置管理器。线程安全（读写加锁）。"""

    _instance: "ConfigManager | None" = None
    _lock = threading.Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> "ConfigManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config_path: str | None = None) -> None:
        # __init__ 可能被多次调用（单例），用标志位避免重复初始化。
        if getattr(self, "_initialized", False):
            return
        # 配置文件路径：默认 <项目根>/config/settings.json
        if config_path is None:
            project_root = self._project_root()
            config_path = os.path.join(project_root, "config", "settings.json")
        self._config_path = config_path
        self._data: dict = {}
        self._listeners: list[Callable[[str], None]] = []
        self._io_lock = threading.Lock()
        self.reload()

    @staticmethod
    def _project_root() -> str:
        # app/core/config_manager.py -> 上溯三级得到项目根
        # （core -> app -> <项目根>）
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # ---- 读写 ----
    def reload(self) -> None:
        """重新从磁盘加载配置，与默认值深合并。"""
        with self._io_lock:
            data = copy.deepcopy(DEFAULT_CONFIG)
            if os.path.isfile(self._config_path):
                try:
                    with open(self._config_path, "r", encoding="utf-8") as f:
                        file_data = json.load(f)
                    data = _deep_merge(DEFAULT_CONFIG, file_data)
                except (json.JSONDecodeError, OSError):
                    # 文件损坏时回退到默认配置，不抛异常（保证应用可启动）
                    data = copy.deepcopy(DEFAULT_CONFIG)
            self._data = data

    def save(self) -> None:
        """持久化当前配置到磁盘。"""
        with self._io_lock:
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)

    # ---- 路径式访问 ----
    def get(self, dotted_key: str, default: Any = None) -> Any:
        """按点分路径取值，如 get("detection.conf")。"""
        node: Any = self._data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted_key: str, value: Any, autosave: bool = False) -> None:
        """按点分路径设值。autosave=True 时立即写盘。"""
        parts = dotted_key.split(".")
        node = self._data
        for part in parts[:-1]:
            if part not in node or not isinstance(node[part], dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
        if autosave:
            self.save()
        self._notify(dotted_key)

    def as_dict(self) -> dict:
        """返回配置的深拷贝（防止外部误改内部状态）。"""
        return copy.deepcopy(self._data)

    def reset_defaults(self, autosave: bool = False) -> None:
        """恢复默认配置。"""
        self._data = copy.deepcopy(DEFAULT_CONFIG)
        if autosave:
            self.save()
        self._notify("*")

    # ---- 变更通知 ----
    def add_listener(self, callback: Callable[[str], None]) -> None:
        """注册变更回调，回调接收变更的点分路径（'*' 表示全量变更）。"""
        self._listeners.append(callback)

    def _notify(self, dotted_key: str) -> None:
        for cb in list(self._listeners):
            try:
                cb(dotted_key)
            except Exception:
                # 监听器异常不影响配置流程
                pass

    # ---- 导入 / 导出 / 校验 ----
    def export_to_file(self, path: str) -> bool:
        """导出当前配置全量到指定 JSON 文件。返回是否成功。"""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.as_dict(), f, ensure_ascii=False, indent=2)
            return True
        except OSError:
            return False

    def import_from_file(self, path: str) -> tuple[bool, list[str]]:
        """从 JSON 文件导入配置。

        流程：读文件 → JSON 解析 → 逐字段校验（非法值用默认替换）→ 深合并 →
        save() → 通知监听器('*')。
        返回 (是否成功, 被重置的字段 dotted_key 列表)。
        成功但无重置字段时第二项为空列表。
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False, []
        if not isinstance(data, dict):
            return False, []
        # 先校验：非法字段替换为默认 + 记录被重置路径
        validated, reset_keys = self._validate_config(data)
        # 与默认值深合并（保证缺字段补默认），再覆盖校验后的值
        merged = _deep_merge(DEFAULT_CONFIG, validated)
        with self._io_lock:
            self._data = merged
        self.save()
        self._notify("*")
        return True, reset_keys

    @staticmethod
    def _validate_config(data: dict) -> tuple[dict, list[str]]:
        """校验配置 dict，返回 (校正后的dict, 被重置字段路径列表)。

        校验基于 _FIELD_RULES 规则表（dotted_key → 校验函数）。
        非法字段：用 DEFAULT_CONFIG 对应值替换 + 记入 reset_keys。
        缺失字段不报错（由后续深合并补默认）。
        """
        result = copy.deepcopy(data)
        reset_keys: list[str] = []
        for dotted, rule in _FIELD_RULES.items():
            # 取当前值（不存在则跳过，交给深合并补默认）
            parts = dotted.split(".")
            node: Any = result
            present = True
            for p in parts[:-1]:
                if not isinstance(node, dict) or p not in node:
                    present = False
                    break
                node = node[p]
            if not present or not isinstance(node, dict) or parts[-1] not in node:
                continue
            value = node[parts[-1]]
            if not rule(value):
                # 非法：用默认值替换
                default_node: Any = DEFAULT_CONFIG
                for p in parts:
                    default_node = default_node[p]
                node[parts[-1]] = copy.deepcopy(default_node)
                reset_keys.append(dotted)
        return result, reset_keys


# ---- 校验规则表 ----
# dotted_key -> 校验函数（返回 True=合法）。
# 覆盖有明确类型/范围约束的字段；纯路径字符串只校验非空+是 str。
def _is_bool(v: Any) -> bool:
    return isinstance(v, bool)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_str(v: Any) -> bool:
    return isinstance(v, str)


def _is_color(v: Any) -> bool:
    """合法 #RRGGBB 颜色。"""
    if not isinstance(v, str) or not v.startswith("#"):
        return False
    h = v[1:]
    return len(h) == 6 and all(c in "0123456789abcdefABCDEF" for c in h)


def _is_int_list_or_none(v: Any) -> bool:
    """检测/报警类别：None 或 int 列表。"""
    return v is None or (isinstance(v, list) and all(isinstance(x, int) for x in v))


_FIELD_RULES: dict[str, Callable[[Any], bool]] = {
    # 检测
    "detection.model_path": lambda v: isinstance(v, str) and v.strip(),
    "detection.conf": lambda v: _is_number(v) and 0.0 <= v <= 1.0,
    "detection.iou": lambda v: _is_number(v) and 0.0 <= v <= 1.0,
    "detection.imgsz": lambda v: _is_number(v) and 320 <= v <= 1280,
    "detection.classes": _is_int_list_or_none,
    "detection.device": lambda v: v in ("cpu", "cuda:0", "cuda:1"),
    "detection.show_masks": _is_bool,
    "detection.show_boxes": _is_bool,
    "detection.show_labels": _is_bool,
    # 报警
    "alarm.enabled_visual": _is_bool,
    "alarm.enabled_sound": _is_bool,
    "alarm.enabled_snapshot": _is_bool,
    "alarm.enabled_log": _is_bool,
    "alarm.enabled_clip": _is_bool,
    "alarm.popup": _is_bool,
    "alarm.cooldown_seconds": lambda v: _is_number(v) and v >= 0.0,
    "alarm.clip_pre_seconds": lambda v: _is_number(v) and 0.0 <= v <= 60.0,
    "alarm.clip_post_seconds": lambda v: _is_number(v) and 0.0 <= v <= 60.0,
    "alarm.dwell_seconds": lambda v: _is_number(v) and 0.0 <= v <= 300.0,
    "alarm.dwell_grace": lambda v: _is_number(v) and 0.0 <= v <= 60.0,
    "alarm.classes": _is_int_list_or_none,
    "alarm.sound_file": _is_str,
    # ROI
    "roi.line_color": _is_color,
    "roi.fill_alpha": lambda v: _is_number(v) and 0 <= v <= 255,
    "roi.line_width": lambda v: _is_number(v) and v >= 0,
    # 外观
    "appearance.theme": lambda v: v in ("dark", "light"),
    "appearance.font_size": lambda v: _is_number(v) and 10 <= v <= 20,
    "appearance.primary_color": _is_color,
    # 路径（仅校验是字符串）
    "paths.snapshots_dir": _is_str,
    "paths.clips_dir": _is_str,
    "paths.logs_dir": _is_str,
    "paths.history_dir": _is_str,
}


def get_config(config_path: str | None = None) -> ConfigManager:
    """获取全局单例 ConfigManager。"""
    return ConfigManager(config_path)
