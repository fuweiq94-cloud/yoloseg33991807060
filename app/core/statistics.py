"""统计聚合：累积检测/报警数据，提供图表查询接口。

记录每次采样的 (timestamp, 各类计数, 报警数)，支持：
- class_counts():  去重累计各类计数（柱状图）—— 按唯一 track_id 去重
- time_series():   目标数随时间变化（折线图）—— 当前帧瞬时数
- class_ratio():   各类占比（饼图）
- alarm_trend():   报警数随时间变化（折线图）

去重语义：同一 track_id（同一目标）在多帧出现只计一次，
避免『一个人站 1000 帧被统计成 1000 个目标』。

持久化到 data/logs/stats.csv（轻量，无需数据库）。线程安全。
"""
from __future__ import annotations

import csv
import os
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class _Sample:
    timestamp: float
    counts: dict[int, int]   # 当前帧各类瞬时计数（time_series 折线用）
    alarms: int


class StatsCollector:
    """统计采集器，线程安全。"""

    def __init__(self, logs_dir: str = "data/logs", names: dict[int, str] | None = None) -> None:
        self._logs_dir = logs_dir
        self._names: dict[int, str] = names or {}
        self._lock = threading.Lock()
        # 限制最近 30 分钟采样（约 30fps × 1800s），防止无界增长拖慢图表查询。
        # 旧采样从左端自动丢弃。
        self._samples: deque = deque(maxlen=54000)
        # 去重累计：track_id -> cls_id；同一 ID 只计入一次
        self._seen_ids: dict[int, int] = {}
        # _class_total 现在反映去重后的『不同目标数』
        self._class_total: dict[int, int] = defaultdict(int)
        self._alarm_total: int = 0
        self._csv_path: str = os.path.join(logs_dir, "stats.csv")
        # CSV 批量缓冲：攒到阈值或 flush() 时一次性写入，避免每帧 open/close。
        self._csv_buffer: list[_Sample] = []
        self._csv_flush_threshold: int = 60  # 约 2 秒（@30fps）落盘一次
        self._ensure_csv_header()
        # 启动即从 CSV 读回最近 30 分钟采样，让折线/报警趋势/累计计数不因重启清零。
        # 读回是按帧累计（CSV 无 track_id，无法去重），deque(maxlen) 自然截断超容量旧数据。
        self._load_recent_from_csv()

    def set_names(self, names: dict[int, str]) -> None:
        with self._lock:
            self._names = dict(names)

    # ---- 写入 ----
    def record(
        self,
        timestamp: float,
        counts: dict[int, int],
        alarms: int,
        track_ids: list[int] | None = None,
        clss: list[int] | None = None,
    ) -> None:
        """记录一次采样。

        counts: 当前帧各类瞬时计数（用于 time_series 折线）。
        track_ids + clss: 本帧每个目标的 (track_id, cls_id)；
            若提供了 track_id，按唯一 ID 去重累计到 _class_total，
            使得柱状/饼图反映『累计出现过的不同目标数』。
        无 track_id（单图检测/未启用跟踪）时，退化为按帧累计。
        """
        sample = _Sample(timestamp=timestamp, counts=dict(counts), alarms=alarms)
        with self._lock:
            self._samples.append(sample)
            # 去重累计：只有新出现的 track_id 才计入 class_total
            if track_ids and clss and len(track_ids) == len(clss):
                for tid, cid in zip(track_ids, clss):
                    tid_i = int(tid)
                    if tid_i < 0:
                        # ID 无效（跟踪器未就绪），退化为按帧计数
                        self._class_total[int(cid)] += 1
                        continue
                    if tid_i in self._seen_ids:
                        continue
                    self._seen_ids[tid_i] = int(cid)
                    self._class_total[int(cid)] += 1
            else:
                # 未提供跟踪信息，保持旧行为：按帧累计
                for cid, n in counts.items():
                    self._class_total[cid] += n
            self._alarm_total += alarms
            # CSV 批量缓冲：攒够阈值才落盘，避免每帧 open/close 拖慢推理线程
            self._csv_buffer.append(sample)
            need_flush = len(self._csv_buffer) >= self._csv_flush_threshold
        # 落盘在锁外执行（磁盘 IO 不必持锁）
        if need_flush:
            self._flush_csv()

    def flush(self) -> None:
        """强制把缓冲区剩余采样写入 CSV。停止采集时应调用，避免丢数据。"""
        self._flush_csv()

    def _flush_csv(self) -> None:
        """把 _csv_buffer 里的采样批量写入 CSV。线程安全（锁内取快照）。"""
        with self._lock:
            if not self._csv_buffer:
                return
            batch = self._csv_buffer
            self._csv_buffer = []
        try:
            with open(self._csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                for sample in batch:
                    dt = datetime.fromtimestamp(sample.timestamp).strftime("%Y-%m-%d %H:%M:%S")
                    if not sample.counts:
                        writer.writerow([f"{sample.timestamp:.3f}", dt, "", "", 0, sample.alarms])
                    for cid, n in sample.counts.items():
                        writer.writerow([
                            f"{sample.timestamp:.3f}",
                            dt,
                            cid,
                            self._names.get(cid, str(cid)),
                            n,
                            sample.alarms,
                        ])
        except OSError:
            pass

    def _ensure_csv_header(self) -> None:
        try:
            os.makedirs(self._logs_dir, exist_ok=True)
            if not os.path.isfile(self._csv_path):
                with open(self._csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["timestamp", "datetime", "class_id", "class_name", "count", "alarms"])
        except OSError:
            pass

    def _append_csv(self, sample: _Sample) -> None:
        # 已被 _flush_csv 批量写入取代，保留空实现避免外部（如有）调用报错。
        # 真正的写入走 record() -> 缓冲 -> _flush_csv()。
        pass

    def _load_recent_from_csv(self, recent_seconds: float = 1800.0) -> None:
        """从 stats.csv 读回最近 recent_seconds 秒的采样，重建内存状态。

        重建内容（启动即恢复，让重启不丢统计）：
        - _samples（折线/报警趋势用）：同 timestamp 多行聚合成单 _Sample
        - _class_total：按帧累计（每行 count 累加，CSV 无 track_id 无法去重）
        - _alarm_total：各帧 alarms 之和（同帧多行只算一次）

        容错：CSV 损坏/格式错/空文件静默跳过，绝不影响启动。
        只读 recent_seconds（默认 1800=30 分钟）内，避免内存无界增长。
        deque(maxlen=54000) 会自然截断超容量的旧采样。
        _seen_ids 不重建（CSV 无 track_id），新检测的去重照常工作。
        """
        if not os.path.isfile(self._csv_path):
            return
        import time as _time
        cutoff = _time.time() - recent_seconds
        # 按 timestamp 聚合：{ts: {"counts": {cid:n}, "alarms": int}}
        agg: dict[float, dict] = {}
        try:
            with open(self._csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None)  # 跳过表头
                for row in reader:
                    if len(row) < 6:
                        continue
                    try:
                        ts = float(row[0])
                    except (ValueError, TypeError):
                        continue
                    if ts < cutoff:
                        continue  # 超出近期窗口，跳过
                    try:
                        alarms = int(float(row[5]))
                    except (ValueError, TypeError):
                        alarms = 0
                    entry = agg.setdefault(ts, {"counts": {}, "alarms": 0})
                    entry["alarms"] = alarms  # 同帧各行 alarms 相同，覆盖即可
                    cid_str = row[2].strip()
                    if cid_str:
                        try:
                            cid = int(float(cid_str))
                            n = int(float(row[4]))
                        except (ValueError, TypeError):
                            continue
                        entry["counts"][cid] = entry["counts"].get(cid, 0) + n
        except OSError:
            return
        # 按时间顺序重建内存状态
        with self._lock:
            for ts in sorted(agg.keys()):
                e = agg[ts]
                self._samples.append(_Sample(timestamp=ts, counts=e["counts"], alarms=e["alarms"]))
                # 按帧累计 _class_total（无 track_id 去重）
                for cid, n in e["counts"].items():
                    self._class_total[cid] += n
                self._alarm_total += e["alarms"]
        # deque(maxlen) 自动截断超容量的最旧采样（_samples），无需手动处理

    # ---- 查询 ----
    def class_counts(self) -> dict[str, int]:
        """累计各类计数，返回 {类名: 数量}。"""
        with self._lock:
            return {self._names.get(cid, str(cid)): n for cid, n in self._class_total.items()}

    def class_ratio(self) -> dict[str, float]:
        """各类占比 {类名: 0-1}。"""
        with self._lock:
            total = sum(self._class_total.values())
            if total == 0:
                return {}
            return {self._names.get(cid, str(cid)): n / total for cid, n in self._class_total.items()}

    def time_series(self, bin_seconds: int = 60) -> tuple[list[str], list[int]]:
        """目标总数随时间变化，按 bin_seconds 分箱。
        返回 (时间标签列表, 目标数列表)。"""
        with self._lock:
            samples = list(self._samples)
        # 每帧目标总数（各类瞬时计数之和）作为桶聚合值
        return self._bucketize(samples, lambda s: sum(s.counts.values()), bin_seconds)

    def alarm_trend(self, bin_seconds: int = 60) -> tuple[list[str], list[int]]:
        """报警数随时间变化，按 bin_seconds 分箱。"""
        with self._lock:
            samples = list(self._samples)
        # 仅累计发生报警的采样；无报警的采样贡献 0，跳过以与原行为一致
        return self._bucketize(
            [s for s in samples if s.alarms > 0],
            lambda s: s.alarms,
            bin_seconds,
        )

    @staticmethod
    def _bucketize(samples, value_of, bin_seconds: int) -> tuple[list[str], list[int]]:
        """把采样按 bin_seconds 时间桶聚合。value_of: sample -> 该采样的贡献值。"""
        if not samples:
            return [], []
        buckets: dict[int, int] = defaultdict(int)
        for s in samples:
            bucket = int(s.timestamp // bin_seconds)
            buckets[bucket] += value_of(s)
        sorted_keys = sorted(buckets.keys())
        labels = [datetime.fromtimestamp(k * bin_seconds).strftime("%H:%M") for k in sorted_keys]
        values = [buckets[k] for k in sorted_keys]
        return labels, values

    @property
    def alarm_total(self) -> int:
        with self._lock:
            return self._alarm_total

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._seen_ids.clear()
            self._class_total.clear()
            self._alarm_total = 0
