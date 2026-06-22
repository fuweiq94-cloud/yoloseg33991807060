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
        "sound_file": "assets/sounds/alarm.wav",
        "cooldown_seconds": 3.0,
        "clip_pre_seconds": 2.0,
        "clip_post_seconds": 2.0,
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


def get_config(config_path: str | None = None) -> ConfigManager:
    """获取全局单例 ConfigManager。"""
    return ConfigManager(config_path)
