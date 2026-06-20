#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键创建虚拟环境并安装依赖。

用法：
    python setup_env.py           # 默认创建 .venv 并安装 requirements.txt
    python setup_env.py myenv     # 指定虚拟环境名

脚本逻辑：
    1. 用当前 Python 创建虚拟环境（默认 .venv）
    2. 升级 pip
    3. 安装 requirements.txt 中的依赖
    4. 校验关键依赖是否可导入

跨平台：Windows 用 .venv\\Scripts\\，类 Unix 用 .venv/bin/。
"""
from __future__ import annotations

import os
import subprocess
import sys

# 脚本所在目录即项目根
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENV = ".venv"


def main() -> int:
    env_name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ENV
    env_dir = os.path.join(PROJECT_ROOT, env_name)

    print("=" * 60)
    print(f" 项目根目录 : {PROJECT_ROOT}")
    print(f" Python     : {sys.executable} ({sys.version.split()[0]})")
    print(f" 虚拟环境   : {env_dir}")
    print("=" * 60)

    # ---- 1. 创建虚拟环境 ----
    if os.path.isdir(os.path.join(env_dir, "Scripts")) or os.path.isdir(
        os.path.join(env_dir, "bin")
    ):
        print(f"\n[1/4] 虚拟环境已存在，跳过创建: {env_dir}")
    else:
        print(f"\n[1/4] 正在创建虚拟环境 ...")
        rc = subprocess.call([sys.executable, "-m", "venv", env_dir])
        if rc != 0:
            print(f"错误：创建虚拟环境失败（退出码 {rc}）")
            return 1
        print(f"      虚拟环境已创建: {env_dir}")

    # ---- 解析虚拟环境的 python / pip 路径 ----
    if os.name == "nt":
        py_exe = os.path.join(env_dir, "Scripts", "python.exe")
    else:
        py_exe = os.path.join(env_dir, "bin", "python")

    if not os.path.isfile(py_exe):
        print(f"错误：找不到虚拟环境的 python: {py_exe}")
        return 1

    # ---- 2. 升级 pip ----
    print("\n[2/4] 正在升级 pip ...")
    rc = subprocess.call(
        [py_exe, "-m", "pip", "install", "--upgrade", "pip"],
    )
    if rc != 0:
        print(f"警告：升级 pip 失败（退出码 {rc}），继续安装依赖 ...")
    else:
        print("      pip 已升级")

    # ---- 3. 安装依赖 ----
    req_file = os.path.join(PROJECT_ROOT, "requirements.txt")
    if not os.path.isfile(req_file):
        print(f"\n错误：找不到 requirements.txt: {req_file}")
        return 1

    print(f"\n[3/4] 正在安装依赖（{os.path.basename(req_file)}）...")
    print("      首次安装会下载 torch 等大包，请耐心等待 ...")
    rc = subprocess.call(
        [py_exe, "-m", "pip", "install", "-r", req_file],
    )
    if rc != 0:
        print(f"\n错误：安装依赖失败（退出码 {rc}）")
        return 1
    print("      依赖安装完成")

    # ---- 4. 校验关键依赖 ----
    print("\n[4/4] 校验关键依赖可导入 ...")
    checks = [
        ("ultralytics", "YOLO 实例分割推理"),
        ("torch", "PyTorch 推理后端"),
        ("cv2", "OpenCV 图像/视频"),
        ("PyQt5", "GUI 框架"),
        ("PyQt5.QtSvg", "SVG 图标渲染"),
        ("pyqtgraph", "统计图表"),
        ("shapely", "ROI 几何判定"),
        ("lap", "目标跟踪（BoT-SORT）"),
    ]
    failed = []
    for mod, desc in checks:
        rc = subprocess.call(
            [py_exe, "-c", f"import {mod.split('.')[0]}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # 对子模块单独测一次
        if rc == 0 and "." in mod:
            rc = subprocess.call(
                [py_exe, "-c", f"import {mod}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        status = "OK" if rc == 0 else "失败"
        if rc != 0:
            failed.append(mod)
        print(f"      [{status}] {mod:<14} {desc}")

    # ---- 完成 ----
    print("\n" + "=" * 60)
    if failed:
        print(f" 警告：以下依赖导入失败，请手动检查: {', '.join(failed)}")
        print("=" * 60)
        return 1

    print(" 全部依赖安装并校验通过！")
    print(" 启动程序：")
    if os.name == "nt":
        print(f"   {env_name}\\Scripts\\python.exe run.py")
    else:
        print(f"   {env_name}/bin/python run.py")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
