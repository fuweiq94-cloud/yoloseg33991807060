"""ROI（感兴趣区域）管理：多边形区域 + 进入判定。

使用 shapely 做点-多边形包含判定。判定规则：检测框中心点落入 ROI 即视为"进入"。
支持多个 ROI，每个有唯一 id。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.validation import make_valid


@dataclass
class RoiRegion:
    """单个多边形 ROI。points 为像素坐标列表 [(x,y), ...]。

    color 为该 ROI 的显示颜色（hex 字符串，如 "#ef4444"），用于画布绘制与
    区域列表的颜色块。新建时由 RoiManager.add 自动分配；旧数据无 color 时
    用默认色。
    """

    points: list[tuple[float, float]]
    roi_id: int
    label: str = ""
    color: str = ""
    _polygon: Polygon | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._build()

    def _build(self) -> None:
        """构建 shapely 多边形；自交/无效时尝试修复，仍无效则退化为空多边形。"""
        if len(self.points) >= 3:
            try:
                poly = Polygon(self.points)
                if not poly.is_valid:
                    poly = make_valid(poly)
                self._polygon = poly if poly.is_valid else None
            except Exception:
                self._polygon = None
        else:
            self._polygon = None

    @property
    def polygon(self) -> Polygon | None:
        return self._polygon

    def contains(self, cx: float, cy: float) -> bool:
        """中心点是否在 ROI 内。"""
        if self._polygon is None:
            return False
        return self._polygon.contains(Point(cx, cy))

    @property
    def valid(self) -> bool:
        return self._polygon is not None

    @property
    def area(self) -> float:
        """多边形面积（像素²）。无效多边形返回 0。"""
        if self._polygon is None:
            return 0.0
        try:
            return float(self._polygon.area)
        except Exception:
            return 0.0

    def to_dict(self) -> dict:
        return {"roi_id": self.roi_id, "label": self.label, "color": self.color, "points": self.points}


class RoiManager:
    """管理多个 RoiRegion。提供 violators() 找出进入任意 ROI 的检测框索引。"""

    # ROI 颜色调色板：每个新建 ROI 按顺序循环分配一个颜色，使相邻区域颜色区分明显。
    # 选色兼顾明暗主题可读性，避开纯红（与违反报警框冲突）。
    ROI_COLORS = [
        "#3b82f6",  # 蓝
        "#22c55e",  # 绿
        "#f59e0b",  # 琥珀
        "#a855f7",  # 紫
        "#06b6d4",  # 青
        "#ec4899",  # 粉
        "#14b8a6",  # 蓝绿
        "#eab308",  # 黄
    ]

    def __init__(self) -> None:
        self._regions: list[RoiRegion] = []
        self._next_id: int = 1
        # 触发报警的类别白名单。None 或空集 = 所有类别都报警（向后兼容）。
        # 仅过滤"是否报警"，不影响 ROI 判定/红框高亮/统计计数。
        self._alarm_classes: set[int] | None = None
        # 驻留判定：目标在 ROI 内连续停留 ≥ dwell_seconds 才报警。
        # dwell_seconds=0 = 关闭（瞬时报警，向后兼容）。
        # grace = 离开后的宽限期（抗检测抖动：漏检 grace 秒内仍算连续）。
        self._dwell_seconds: float = 0.0
        self._dwell_grace: float = 1.0
        # 驻留计时状态：{(track_id, roi_id): {"enter": 首次进入时刻, "last": 最后在场时刻}}
        self._dwell_state: dict[tuple[int, int], dict[str, float]] = {}

    # ---- 增删查 ----
    def add(self, points: list[tuple[float, float]], label: str = "", color: str = "") -> RoiRegion:
        """添加一个多边形 ROI，返回新建的 RoiRegion。

        color 留空时按当前区域数量从 ROI_COLORS 循环取色，保证每个 ROI 颜色不同。
        """
        if not color:
            color = self.ROI_COLORS[(len(self._regions)) % len(self.ROI_COLORS)]
        region = RoiRegion(points=points, roi_id=self._next_id,
                           label=label or f"ROI{self._next_id}", color=color)
        self._regions.append(region)
        self._next_id += 1
        return region

    def remove(self, roi_id: int) -> bool:
        for i, r in enumerate(self._regions):
            if r.roi_id == roi_id:
                self._regions.pop(i)
                return True
        return False

    def set_color(self, roi_id: int, color: str) -> bool:
        """修改指定 ROI 的颜色（hex，如 "#ef4444"）。

        RoiRegion 是普通 dataclass，直接赋值 color 即可，无需重建 polygon。
        返回是否找到并修改成功。
        """
        for r in self._regions:
            if r.roi_id == roi_id:
                r.color = color
                return True
        return False

    def clear(self) -> None:
        self._regions.clear()
        self._next_id = 1

    @property
    def regions(self) -> list[RoiRegion]:
        return list(self._regions)

    def __len__(self) -> int:
        return len(self._regions)

    # ---- 报警类别过滤 ----
    def set_alarm_classes(self, classes) -> None:
        """设置触发报警的类别白名单。

        None 或空集 = 所有类别都报警（向后兼容；也防止用户误清空后突然全静默）。
        仅影响报警判定，不影响 ROI 命中/红框高亮/统计计数。
        """
        self._alarm_classes = set(int(c) for c in classes) if classes else None

    @property
    def alarm_classes(self) -> set[int] | None:
        """当前报警类别白名单。None = 不过滤（全报警）。"""
        return set(self._alarm_classes) if self._alarm_classes is not None else None

    # ---- 驻留判定 ----
    def set_dwell(self, seconds: float, grace: float = 1.0) -> None:
        """设置驻留阈值（秒）。0 = 关闭（瞬时报警，向后兼容）。

        seconds: 目标在 ROI 内连续停留 ≥ 此值才报警。
        grace:   离开后的宽限期——漏检 grace 秒内重新出现仍算连续（抗检测抖动）。
        切换阈值时清空计时状态，避免旧状态干扰。
        """
        self._dwell_seconds = max(0.0, float(seconds))
        self._dwell_grace = max(0.0, float(grace))
        self._dwell_state.clear()

    def filter_dwell(
        self,
        violators: list[tuple[int, int]],
        track_ids,
        ts: float,
    ) -> list[tuple[int, int]]:
        """从 violators [(box_idx, roi_id)] 中筛出「满足驻留阈值」的目标。

        返回满足驻留的 violators 子集（用于触发报警）。红框高亮/统计仍用原始 violators。

        - dwell_seconds=0：直接返回全部（关闭，向后兼容）。
        - 无 track_id（track_ids 空/全 -1，如单图）：返回全部（无法判驻留，瞬时报警）。
        - 每个 (track_id, roi_id)：
          - 在场：更新 last_seen；首次进入记 enter_ts。
          - 本帧不在场但 last_seen 在 grace 内：保留计时（宽容漏检）。
          - 离开超 grace：重置该键。
          - 累计在场 ts - enter ≥ dwell_seconds：满足，保留。
        """
        if self._dwell_seconds <= 0.0:
            return list(violators)  # 关闭，不过滤
        # 无有效 track_id：无法判定驻留，退化为瞬时（返回全部）
        tids = list(track_ids) if track_ids is not None and len(track_ids) else []
        if not tids or all(int(t) < 0 for t in tids):
            return list(violators)

        # 本帧在场的 (tid, rid) 集合
        present: set[tuple[int, int]] = set()
        for box_idx, roi_id in violators:
            if box_idx < len(tids):
                tid = int(tids[box_idx])
                if tid >= 0:
                    present.add((tid, roi_id))

        # 更新在场目标的计时
        for key in present:
            st = self._dwell_state.get(key)
            if st is None:
                self._dwell_state[key] = {"enter": ts, "last": ts}
            else:
                st["last"] = ts  # enter 保持首次进入时刻（连续未中断）

        # 清除离开超 grace 的目标（真正离开）
        expired = [k for k, st in self._dwell_state.items()
                   if k not in present and (ts - st["last"]) > self._dwell_grace]
        for k in expired:
            del self._dwell_state[k]

        # 筛出满足驻留阈值的 violators
        result = []
        for box_idx, roi_id in violators:
            if box_idx < len(tids):
                tid = int(tids[box_idx])
                if tid >= 0:
                    st = self._dwell_state.get((tid, roi_id))
                    if st is not None and (ts - st["enter"]) >= self._dwell_seconds:
                        result.append((box_idx, roi_id))
                        continue
            # 无 track_id 的框：dwell_seconds>0 时无法判定，不触发（保守：宁可不报）
        return result

    # ---- 判定 ----
    def violators(
        self,
        centers: np.ndarray,
        clss: np.ndarray | None = None,
    ) -> list[tuple[int, int]]:
        """返回 [(box_idx, roi_id), ...]，表示第 box_idx 个框的中心落入了 roi_id 区域。

        centers: (N,2) 检测框中心点。
        clss:    (N,) 类别（可选，预留按类过滤）。
        """
        hits: list[tuple[int, int]] = []
        if len(self._regions) == 0 or centers is None or len(centers) == 0:
            return hits
        centers = np.asarray(centers, dtype=np.float64)
        for box_idx in range(len(centers)):
            cx, cy = float(centers[box_idx, 0]), float(centers[box_idx, 1])
            for region in self._regions:
                if region.contains(cx, cy):
                    hits.append((box_idx, region.roi_id))
                    break  # 一个框命中任意 ROI 即算违反，不重复
        return hits

    # ---- 序列化 ----
    def to_list(self) -> list[dict]:
        return [r.to_dict() for r in self._regions]

    def from_list(self, data: list[dict]) -> None:
        """从列表恢复（最大 roi_id 续号）。

        兼容旧数据：无 color 字段时按加载顺序从 ROI_COLORS 补色。
        """
        self.clear()
        max_id = 0
        for idx, item in enumerate(data):
            pts = [(float(x), float(y)) for x, y in item.get("points", [])]
            rid = int(item.get("roi_id", self._next_id))
            label = item.get("label", "")
            color = item.get("color", "") or self.ROI_COLORS[idx % len(self.ROI_COLORS)]
            region = RoiRegion(points=pts, roi_id=rid, label=label, color=color)
            self._regions.append(region)
            max_id = max(max_id, rid)
        self._next_id = max_id + 1 if self._regions else 1
