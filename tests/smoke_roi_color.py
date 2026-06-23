"""ROI 多色 + 面积冲烟测试。

验证：
  1. RoiManager.add 自动按顺序分配不同颜色（循环调色板）。
  2. 颜色持久化：to_list 含 color，from_list 能还原，旧数据无 color 时补色。
  3. RoiRegion.area 返回非零面积（正方形 100x100 = 10000）。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app.core.roi import RoiManager, RoiRegion


def test_color_auto_assign():
    m = RoiManager()
    r1 = m.add([(0, 0), (10, 0), (10, 10), (0, 10)])
    r2 = m.add([(20, 20), (30, 20), (30, 30), (20, 30)])
    r3 = m.add([(40, 40), (50, 40), (50, 50), (40, 50)])
    print(f"[颜色] r1={r1.color} r2={r2.color} r3={r3.color}")
    assert r1.color and r2.color and r3.color, "颜色应自动分配"
    assert len({r1.color, r2.color, r3.color}) == 3, "前三个颜色应互不相同"
    # 颜色应在调色板内
    assert r1.color in RoiManager.ROI_COLORS
    print("[OK] 自动分配多色 通过")


def test_persist_roundtrip():
    m = RoiManager()
    m.add([(0, 0), (10, 0), (10, 10), (0, 10)], label="A")
    m.add([(20, 20), (30, 20), (30, 30), (20, 30)], label="B")
    data = m.to_list()
    print(f"[持久化] to_list: {data[0]}")
    assert all("color" in d for d in data), "to_list 应含 color 字段"
    # 还原
    m2 = RoiManager()
    m2.from_list(data)
    assert [r.color for r in m2.regions] == [r.color for r in m.regions], "颜色还原不一致"
    print("[OK] 颜色持久化 通过")


def test_legacy_data_compat():
    """旧数据（无 color）导入时应自动补色，不报错。"""
    legacy = [
        {"roi_id": 1, "label": "ROI1", "points": [[0, 0], [10, 0], [10, 10], [0, 10]]},
        {"roi_id": 2, "label": "ROI2", "points": [[20, 20], [30, 20], [30, 30], [20, 30]]},
    ]
    m = RoiManager()
    m.from_list(legacy)
    colors = [r.color for r in m.regions]
    print(f"[兼容] 旧数据补色: {colors}")
    assert all(c for c in colors), "旧数据应补色"
    assert len(set(colors)) == 2, "两个区域应颜色不同"
    print("[OK] 旧数据兼容 通过")


def test_area():
    r = RoiRegion(points=[(0, 0), (100, 0), (100, 100), (0, 100)], roi_id=1, color="#3b82f6")
    print(f"[面积] 100x100 正方形 area={r.area}")
    assert abs(r.area - 10000.0) < 1e-6, "100x100 正方形面积应为 10000"
    print("[OK] 面积计算 通过")


if __name__ == "__main__":
    test_color_auto_assign()
    test_persist_roundtrip()
    test_legacy_data_compat()
    test_area()
    print("\n全部通过 ✅")
