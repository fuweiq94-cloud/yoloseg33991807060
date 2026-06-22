"""识别历史记录管理：图片/视频识别结果的持久化与查询。

每次「保存」操作产生一条 HistoryRecord，落盘到 history_dir：
- 元数据索引 records.json（id/type/时间/来源/文件名/统计）
- 图片文件 img_*.jpg / 视频文件 vid_*.mp4 / 缩略图 *_thumb.jpg

线程安全（读写加锁）。无 Qt 依赖，纯逻辑 + 文件 IO。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Any

import cv2
import numpy as np

from app.utils.logger import get_logger

logger = get_logger()

# 缩略图尺寸（宽×高）
THUMB_W, THUMB_H = 240, 135  # 16:9，比异常列表稍大，网格更清晰


@dataclass
class HistoryRecord:
    """单条历史记录。"""
    id: str                       # 唯一 ID（递增，如 "001"）
    type: str                     # "image" | "video"
    timestamp: float              # 识别时间（Unix 时间戳）
    source_name: str              # 源名称（图片/视频文件名，或 "摄像头0"）
    file: str                     # 相对 history_dir 的文件名（img_xxx.jpg / vid_xxx.mp4）
    thumb: str                    # 相对 history_dir 的缩略图文件名
    counts: dict[int, int] = field(default_factory=dict)   # 各类目标数 {cls_id: n}
    class_names: dict[int, str] = field(default_factory=dict)  # {cls_id: name}，便于 UI 显示
    alarms: int = 0               # 报警数
    duration: float = 0.0         # 视频时长（秒），图片为 0
    note: str = ""                # 备注（可选）

    def time_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def label(self) -> str:
        """主标题：来源名 + 时间。"""
        return f"{self.source_name}"

    def sublabel(self) -> str:
        """副标题：目标数 / 报警数 / 时长。"""
        total = sum(self.counts.values())
        parts = [f"目标 {total}"]
        if self.alarms > 0:
            parts.append(f"报警 {self.alarms}")
        if self.type == "video" and self.duration > 0:
            parts.append(f"{self.duration:.1f}s")
        return " · ".join(parts)


class HistoryManager:
    """历史记录管理器，线程安全单例。"""

    _instance: "HistoryManager | None" = None
    _lock = threading.Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> "HistoryManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, history_dir: str = "data/history") -> None:
        if getattr(self, "_initialized", False):
            return
        self._history_dir = os.path.abspath(history_dir)
        self._records_path = os.path.join(self._history_dir, "records.json")
        self._io_lock = threading.Lock()
        self._records: list[HistoryRecord] = []
        self._next_id: int = 1
        self._initialized = True
        self.reload()

    # ---- 加载/保存索引 ----
    def reload(self) -> None:
        """从磁盘加载 records.json。文件不存在或损坏时初始化为空。"""
        with self._io_lock:
            self._records = []
            self._next_id = 1
            os.makedirs(self._history_dir, exist_ok=True)
            if os.path.isfile(self._records_path):
                try:
                    with open(self._records_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for item in data.get("records", []):
                        self._records.append(HistoryRecord(**item))
                    self._next_id = int(data.get("next_id", len(self._records) + 1))
                except (json.JSONDecodeError, OSError, TypeError) as e:
                    logger.warning("历史记录索引损坏，重新初始化: %s", e)
                    self._records = []
                    self._next_id = 1

    def _save_index(self) -> None:
        """持久化 records.json（调用方需持锁）。"""
        os.makedirs(self._history_dir, exist_ok=True)
        data = {
            "next_id": self._next_id,
            "records": [asdict(r) for r in self._records],
        }
        with open(self._records_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ---- 查询 ----
    def all_records(self) -> list[HistoryRecord]:
        """返回所有记录（按时间倒序，最新的在前）。"""
        with self._io_lock:
            return sorted(self._records, key=lambda r: r.timestamp, reverse=True)

    def get(self, record_id: str) -> HistoryRecord | None:
        with self._io_lock:
            for r in self._records:
                if r.id == record_id:
                    return r
        return None

    def file_path(self, record: HistoryRecord) -> str:
        """记录对应媒体文件的绝对路径。"""
        return os.path.join(self._history_dir, record.file)

    def thumb_path(self, record: HistoryRecord) -> str:
        return os.path.join(self._history_dir, record.thumb)

    # ---- 新增（图片）----
    def add_image(
        self,
        frame_bgr: np.ndarray,
        source_name: str,
        counts: dict[int, int] | None = None,
        class_names: dict[int, str] | None = None,
        alarms: int = 0,
        note: str = "",
    ) -> HistoryRecord:
        """保存一张图片识别结果。返回新建的记录。"""
        ts = time.time()
        ts_str = datetime.fromtimestamp(ts).strftime("%Y%m%d_%H%M%S")
        with self._io_lock:
            rid = f"{self._next_id:04d}"
            self._next_id += 1
        file_name = f"img_{ts_str}_{rid}.jpg"
        thumb_name = f"img_{ts_str}_{rid}_thumb.jpg"
        file_full = os.path.join(self._history_dir, file_name)
        thumb_full = os.path.join(self._history_dir, thumb_name)
        os.makedirs(self._history_dir, exist_ok=True)
        # 写原图
        ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            buf.tofile(file_full)
        else:
            logger.error("历史图片编码失败: %s", file_name)
        # 写缩略图
        self._write_thumb(frame_bgr, thumb_full)
        record = HistoryRecord(
            id=rid, type="image", timestamp=ts, source_name=source_name,
            file=file_name, thumb=thumb_name,
            counts=dict(counts or {}), class_names=dict(class_names or {}),
            alarms=alarms, duration=0.0, note=note,
        )
        with self._io_lock:
            self._records.append(record)
            self._save_index()
        logger.info("历史图片已保存: %s", file_name)
        return record

    # ---- 新增（视频）----
    def add_video(
        self,
        tmp_path: str,
        source_name: str,
        counts: dict[int, int] | None = None,
        class_names: dict[int, str] | None = None,
        alarms: int = 0,
        duration: float = 0.0,
        note: str = "",
    ) -> HistoryRecord | None:
        """把临时录制的视频归档到历史目录，生成缩略图与索引。

        tmp_path: VideoWorker 录制的临时 mp4 路径。
        返回新建的记录；若临时文件无效则返回 None。
        """
        if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) == 0:
            logger.warning("视频归档失败：临时文件无效 %s", tmp_path)
            return None
        ts = time.time()
        ts_str = datetime.fromtimestamp(ts).strftime("%Y%m%d_%H%M%S")
        with self._io_lock:
            rid = f"{self._next_id:04d}"
            self._next_id += 1
        file_name = f"vid_{ts_str}_{rid}.mp4"
        thumb_name = f"vid_{ts_str}_{rid}_thumb.jpg"
        file_full = os.path.join(self._history_dir, file_name)
        thumb_full = os.path.join(self._history_dir, thumb_name)
        os.makedirs(self._history_dir, exist_ok=True)
        # 移动临时文件到正式位置
        try:
            os.replace(tmp_path, file_full)
        except OSError:
            # 跨盘时 replace 失败，退回 copy + delete
            import shutil
            shutil.copyfile(tmp_path, file_full)
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        # 从归档视频取中间帧做缩略图
        self._write_video_thumb(file_full, thumb_full)
        record = HistoryRecord(
            id=rid, type="video", timestamp=ts, source_name=source_name,
            file=file_name, thumb=thumb_name,
            counts=dict(counts or {}), class_names=dict(class_names or {}),
            alarms=alarms, duration=duration, note=note,
        )
        with self._io_lock:
            self._records.append(record)
            self._save_index()
        logger.info("历史视频已归档: %s (%.1fs)", file_name, duration)
        return record

    # ---- 删除 ----
    def delete(self, record_id: str) -> bool:
        """删除一条记录及其媒体文件。"""
        with self._io_lock:
            target = None
            for r in self._records:
                if r.id == record_id:
                    target = r
                    break
            if target is None:
                return False
            self._records.remove(target)
            self._save_index()
        # 删文件（锁外执行，避免 IO 阻塞索引读写）
        for p in (self.file_path(target), self.thumb_path(target)):
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
        logger.info("历史记录已删除: %s", record_id)
        return True

    def clear_all(self) -> int:
        """清空所有历史记录及其文件，返回删除条数。"""
        with self._io_lock:
            count = len(self._records)
            ids = [r.id for r in self._records]
            files = [(self.file_path(r), self.thumb_path(r)) for r in self._records]
            self._records.clear()
            self._next_id = 1
            self._save_index()
        for fp, tp in files:
            for p in (fp, tp):
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                except OSError:
                    pass
        logger.info("已清空全部历史记录: %d 条", count)
        return count

    # ---- 临时文件管理 ----
    def cleanup_temp(self) -> None:
        """清理 history_dir 下残留的临时视频文件（.tmp_vid_*.mp4）。"""
        if not os.path.isdir(self._history_dir):
            return
        removed = 0
        for name in os.listdir(self._history_dir):
            if name.startswith(".tmp_vid_") and name.endswith(".mp4"):
                try:
                    os.remove(os.path.join(self._history_dir, name))
                    removed += 1
                except OSError:
                    pass
        if removed:
            logger.info("清理临时视频文件: %d 个", removed)

    def new_temp_video_path(self) -> str:
        """生成一个新的临时录制文件路径（供 VideoWorker 写入）。"""
        os.makedirs(self._history_dir, exist_ok=True)
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return os.path.join(self._history_dir, f".tmp_vid_{ts_str}.mp4")

    # ---- 内部辅助 ----
    def _write_thumb(self, frame_bgr: np.ndarray, dest: str) -> None:
        """把帧缩放到 THUMB_W×THUMB_H 写为 jpg。"""
        try:
            h, w = frame_bgr.shape[:2]
            scale = min(THUMB_W / w, THUMB_H / h)
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            small = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_AREA)
            # 居中填充到固定尺寸（避免比例不一导致网格参差）
            canvas = np.full((THUMB_H, THUMB_W, 3), 24, dtype=np.uint8)  # 深灰底
            x0 = (THUMB_W - nw) // 2
            y0 = (THUMB_H - nh) // 2
            canvas[y0:y0 + nh, x0:x0 + nw] = small
            ok, buf = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                buf.tofile(dest)
        except Exception as e:
            logger.warning("缩略图生成失败: %s", e)

    def _write_video_thumb(self, video_path: str, dest: str) -> None:
        """从视频取中间帧生成缩略图。"""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.warning("无法打开视频生成缩略图: %s", video_path)
            return
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        mid = n // 2 if n > 0 else 0
        if mid > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, mid)
        ok, frame = cap.read()
        cap.release()
        if ok and frame is not None:
            self._write_thumb(frame, dest)
