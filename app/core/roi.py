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
    """单个多边形 ROI。points 为像素坐标列表 [(x,y), ...]。"""

    points: list[tuple[float, float]]
    roi_id: int
    label: str = ""
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

    def to_dict(self) -> dict:
        return {"roi_id": self.roi_id, "label": self.label, "points": self.points}


class RoiManager:
    """管理多个 RoiRegion。提供 violators() 找出进入任意 ROI 的检测框索引。"""

    def __init__(self) -> None:
        self._regions: list[RoiRegion] = []
        self._next_id: int = 1

    # ---- 增删查 ----
    def add(self, points: list[tuple[float, float]], label: str = "") -> RoiRegion:
        """添加一个多边形 ROI，返回新建的 RoiRegion。"""
        region = RoiRegion(points=points, roi_id=self._next_id, label=label or f"ROI{self._next_id}")
        self._regions.append(region)
        self._next_id += 1
        return region

    def remove(self, roi_id: int) -> bool:
        for i, r in enumerate(self._regions):
            if r.roi_id == roi_id:
                self._regions.pop(i)
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
        """从列表恢复（最大 roi_id 续号）。"""
        self.clear()
        max_id = 0
        for item in data:
            pts = [(float(x), float(y)) for x, y in item.get("points", [])]
            rid = int(item.get("roi_id", self._next_id))
            label = item.get("label", "")
            region = RoiRegion(points=pts, roi_id=rid, label=label)
            self._regions.append(region)
            max_id = max(max_id, rid)
        self._next_id = max_id + 1 if self._regions else 1
