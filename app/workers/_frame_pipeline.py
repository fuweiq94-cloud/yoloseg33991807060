"""Worker 共享的帧处理流水线。

VideoWorker 与 ImageWorker 在「ROI 判定 → 报警聚合触发 → 类别计数」这一段
逻辑完全一致，抽取到此处，保证两条路径行为逐位等价，避免两边各自演化产生分歧。

本模块无 Qt 依赖，仅做纯计算 + 调用注入的 alarm/stats。信号发射仍由各 worker
自行完成（发射顺序、信号类型由 worker 决定）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.alarm import AlarmEngine, AlarmEvent
from app.core.detector import DetectionResult
from app.core.roi import RoiManager
from app.core.statistics import StatsCollector


@dataclass
class TargetInfo:
    """单个检测目标的详情（供检测页目标清单面板显示）。

    与 DetectionResult.boxes 同序，便于对照画布上的检测框。
    """

    track_id: int     # 跟踪 ID（视频源有；图片源为 -1）
    cls_id: int
    cls_name: str
    conf: float


@dataclass
class FrameDetails:
    """单帧的目标详情汇总（供检测页目标详情面板显示，1Hz 节流刷新）。"""

    targets: list[TargetInfo]   # 逐目标，与 boxes 同序
    counts: dict[str, int]      # {类名: 数} 逐类别瞬时计数


@dataclass
class FrameOutcome:
    """单帧处理结果。worker 据此发射对应信号。"""

    centers: object                  # DetectionResult.centers() 的返回值（ndarray）
    violator_indices: list[int]      # 进入 ROI 的检测框索引
    counts: dict[int, int]           # 本帧各类瞬时计数
    alarms_this_frame: int           # 实际触发（未被冷却合并）的报警数
    fired_alarms: list[tuple[int, list[int], list[float]]]
    # 已触发的报警 [(roi_id, cls_ids, confs), ...]，按触发顺序，供 worker 发 alarm_ready
    details: FrameDetails            # 目标详情（供检测页面板显示）


def process_frame(
    result: DetectionResult,
    roi_manager: RoiManager,
    stats: StatsCollector,
    alarm: AlarmEngine | None,
    *,
    use_tracking: bool = False,
    timestamp: float | None = None,
) -> FrameOutcome:
    """对一帧检测结果执行 ROI 判定 + 报警 + 统计采样。

    use_tracking=True 时把 track_ids 透传给 StatsCollector 做去重累计
    （视频流路径）；False 时退化为按帧累计（单图路径）。
    timestamp 默认取当前时间，与原 worker 行为一致。

    副作用：会调用 alarm.trigger(...)（可能触发回调）和 stats.record(...)。
    """
    ts = time.time() if timestamp is None else timestamp

    # ROI 判定
    centers = result.centers()
    violators = roi_manager.violators(centers, result.clss)  # [(box_idx, roi_id), ...]
    violator_indices = [bi for bi, _ in violators]

    # 报警：按 roi 聚合，每个 roi 一次 AlarmEvent
    alarms_this_frame = 0
    fired: list[tuple[int, list[int], list[float]]] = []
    # 报警类别白名单：None = 不过滤（全报警）。仅过滤"是否报警"，不影响
    # violator_indices（红框高亮）和 counts（统计）——它们仍含全部进 ROI 的目标。
    alarm_cls_filter = roi_manager.alarm_classes
    if violators and alarm is not None:
        # 驻留过滤：取出满足驻留阈值的 violators（track_id 可用时才判驻留；
        # 无 track_id/驻留关闭时 filter_dwell 返回全部）。红框/统计仍用原始 violators。
        dwell_passed = roi_manager.filter_dwell(violators, result.track_ids, ts)
        by_roi: dict[int, list[int]] = {}
        for bi, rid in dwell_passed:
            by_roi.setdefault(rid, []).append(bi)
        for rid, idxs in by_roi.items():
            # 按报警类别白名单过滤：只保留启用了报警的类别的框
            if alarm_cls_filter is not None:
                idxs = [bi for bi in idxs if int(result.clss[bi]) in alarm_cls_filter]
            if not idxs:
                continue  # 该 ROI 没有需要报警的目标，跳过（但仍算 violator 红框高亮）
            cls_ids = [int(result.clss[bi]) for bi in idxs]
            confs = [float(result.confs[bi]) for bi in idxs]
            event = AlarmEvent(
                timestamp=ts,
                frame=result.annotated.copy(),
                cls_ids=cls_ids,
                confs=confs,
                roi_id=rid,
                box_indices=list(idxs),
            )
            if alarm.trigger(event):
                alarms_this_frame += 1
                fired.append((rid, cls_ids, confs))

    # 类别瞬时计数
    counts: dict[int, int] = {}
    for cid in result.clss:
        counts[int(cid)] = counts.get(int(cid), 0) + 1

    # 统计采样：跟踪路径透传 track_ids 做去重累计
    if use_tracking:
        tids = [int(t) for t in result.track_ids] if len(result.track_ids) else []
        clss_list = [int(c) for c in result.clss] if len(result.clss) else []
        stats.record(ts, counts, alarms_this_frame, track_ids=tids, clss=clss_list)
    else:
        stats.record(ts, counts, alarms_this_frame)

    # 目标详情（供检测页面板）：逐目标 + 逐类别计数（转成类名，便于人读）
    names = result.names
    targets = [
        TargetInfo(
            # 仅跟踪路径才有可信 track_id；图片源(use_tracking=False)一律 -1，
            # 避免依赖 detector 端把 track_ids 留空这一隐含约定。
            track_id=int(result.track_ids[i]) if (use_tracking and i < len(result.track_ids)) else -1,
            cls_id=int(result.clss[i]),
            cls_name=names.get(int(result.clss[i]), str(result.clss[i])),
            conf=float(result.confs[i]),
        )
        for i in range(len(result.clss))
    ]
    details = FrameDetails(
        targets=targets,
        counts={names.get(cid, str(cid)): n for cid, n in counts.items()},
    )

    return FrameOutcome(
        centers=centers,
        violator_indices=violator_indices,
        counts=counts,
        alarms_this_frame=alarms_this_frame,
        fired_alarms=fired,
        details=details,
    )
