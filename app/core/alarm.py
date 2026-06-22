"""报警引擎：四通道（界面/声音/截图/日志）分发 + 冷却去重。

核心 AlarmEngine 负责：
1. 接收 AlarmEvent
2. 冷却去重：cooldown 秒内同 (roi_id, cls) 合并
3. 按开关分发到各 Channel

Channel 分两类：
- 同步即时通道（Visual/Sound）：直接回调，由 UI 线程处理
- 异步耗时通道（Snapshot/Log）：通过传入的 executor 在后台执行

本模块无 Qt 依赖，executor 由 workers/ui 层注入（如 QThreadPool 或 ThreadPoolExecutor）。
"""
from __future__ import annotations

import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import numpy as np


@dataclass
class AlarmEvent:
    """单次报警事件。"""

    timestamp: float                              # unix 时间戳
    frame: np.ndarray                             # 触发时的 BGR 帧
    cls_ids: list[int]                            # 触发的目标类别 id 列表
    confs: list[float]                            # 对应置信度
    roi_id: int                                   # 触发的 ROI id
    box_indices: list[int]                        # 触发的框索引
    snapshot_path: str | None = field(default=None, repr=False)


# 各通道开关与配置（由 ConfigManager 注入）
@dataclass
class AlarmConfig:
    enabled_visual: bool = True
    enabled_sound: bool = True
    enabled_snapshot: bool = True
    enabled_log: bool = True
    sound_file: str = ""
    cooldown_seconds: float = 3.0
    clip_pre_seconds: float = 2.0
    clip_post_seconds: float = 2.0
    popup: bool = True
    snapshots_dir: str = "data/snapshots"
    logs_dir: str = "data/logs"


# 通道回调签名
VisualCallback = Callable[[AlarmEvent], None]
SoundCallback = Callable[[AlarmEvent], None]
SnapshotCallback = Callable[[AlarmEvent], None]
LogCallback = Callable[[AlarmEvent], None]


class AlarmEngine:
    """报警引擎，线程安全。"""

    def __init__(
        self,
        config: AlarmConfig,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._config = config
        self._executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="alarm")
        self._lock = threading.Lock()
        # 冷却记录：{(roi_id, cls): last_trigger_ts}
        self._last_trigger: dict[tuple[int, int], float] = {}
        # 各通道回调（由 UI/worker 注入）
        self._on_visual: VisualCallback | None = None
        self._on_sound: SoundCallback | None = None
        self._on_snapshot: SnapshotCallback | None = None
        self._on_log: LogCallback | None = None

    # ---- 配置 ----
    def update_config(self, config: AlarmConfig) -> None:
        with self._lock:
            self._config = config

    @property
    def sound_file(self) -> str:
        """当前配置的声音文件路径（供 UI 在主线程播放）。"""
        with self._lock:
            return self._config.sound_file

    # ---- 回调注入 ----
    def set_visual_callback(self, cb: VisualCallback) -> None:
        self._on_visual = cb

    def set_sound_callback(self, cb: SoundCallback) -> None:
        self._on_sound = cb

    def set_snapshot_callback(self, cb: SnapshotCallback) -> None:
        self._on_snapshot = cb

    def set_log_callback(self, cb: LogCallback) -> None:
        self._on_log = cb

    # ---- 触发 ----
    def trigger(self, event: AlarmEvent) -> bool:
        """处理一次报警事件。返回是否真正分发（被冷却合并返回 False）。"""
        cfg = self._config
        now = event.timestamp

        # 冷却去重：按 (roi_id, cls) 组合，每个 cls 独立冷却
        with self._lock:
            should_fire = False
            for cls_id in event.cls_ids:
                key = (event.roi_id, cls_id)
                last = self._last_trigger.get(key, 0.0)
                if now - last >= cfg.cooldown_seconds:
                    self._last_trigger[key] = now
                    should_fire = True
            if not should_fire:
                return False

        # 同步通道：界面、声音（即时反馈，在调用线程执行）
        if cfg.enabled_visual and self._on_visual:
            try:
                self._on_visual(event)
            except Exception:
                pass
        if cfg.enabled_sound and self._on_sound:
            try:
                self._on_sound(event)
            except Exception:
                pass

        # 异步通道：截图、日志（耗时，丢入线程池）
        if cfg.enabled_snapshot and self._on_snapshot:
            self._executor.submit(self._safe_call, self._on_snapshot, event)
        if cfg.enabled_log and self._on_log:
            self._executor.submit(self._safe_call, self._on_log, event)

        return True

    @staticmethod
    def _safe_call(cb: Callable, event: AlarmEvent) -> None:
        try:
            cb(event)
        except Exception:
            pass

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)
