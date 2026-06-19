# 安全识别系统 SecurityVisionSystem

基于 **YOLO26s-seg** + **PyQt5** 的桌面端安全识别系统。支持图片 / 视频 / 实时摄像头检测，
可自定义多边形 ROI 区域，目标进入 ROI 即触发报警，并提供实时统计图表与异常帧回看。

## 功能特性

- **多数据源**：图片、视频文件、实时摄像头（cv2）
- **实例分割检测**：基于 YOLO26s-seg，输出框 + 掩码
- **自定义 ROI**：多边形逐点绘制，支持多个区域
- **进入报警**：检测框中心点落入 ROI 即报警，四通道反馈
  - 界面红色高亮 + 状态灯 + 弹窗
  - 声音报警（Windows winsound）
  - 报警截图 / 录像片段存档
  - 日志记录（csv + app.log）
- **类别筛选**：勾选只检测感兴趣的类别（默认 person）
- **统计图表**：类别计数柱状图、时间趋势折线图、类别占比饼图、报警趋势图
- **异常帧列表**：报警帧自动归档，可点击放大回看
- **独立设置界面**：置信度 / IoU / 设备 / 报警 / 外观 / 路径 全部可视化配置
- **克制工业风 UI**：单一钢蓝主题色，深色主题，无表情符号

## 环境要求

- Windows 10/11（声音报警用 winsound）
- Python 3.10+（本项目使用 3.12）
- 可选：NVIDIA GPU + CUDA（加速推理，无则用 CPU）

## 安装

本项目使用项目内独立虚拟环境 `.venv`（Python 3.12，自包含 ultralytics + torch + 全部依赖）。

```cmd
cd C:\Users\fu\Desktop\ultralytics\security-vision-system
:: 1. 创建虚拟环境（仅需一次）
C:\Users\fu\AppData\Local\Programs\Python\Python312\python.exe -m venv .venv
:: 2. 安装依赖
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 运行

```cmd
.venv\Scripts\python.exe run.py
```

或在已激活该 venv 的终端里：

```cmd
.venv\Scripts\activate
python run.py
```

## 项目结构

```
security-vision-system/
├─ README.md                本文件
├─ requirements.txt         依赖清单
├─ run.py                   启动入口
├─ config/                  配置文件（settings.json / classes.json）
├─ assets/                  资源（icons / sounds / models）
├─ data/                    运行时数据（snapshots / clips / logs）
├─ docs/                    文档（DESIGN / ARCHITECTURE / USER_GUIDE）
└─ app/
   ├─ core/                 业务核心（无 Qt 依赖，可单测）
   ├─ workers/              PyQt 后台线程
   ├─ ui/                   界面（theme / main_window / widgets / dialogs）
   └─ utils/                通用工具
```

详细架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，使用说明见 [docs/USER_GUIDE.md](docs/USER_GUIDE.md)。

## 快速验证

1. 启动后默认选中"摄像头"，点【开始】即可实时检测
2. 在画布上点击画多边形 ROI，双击闭合
3. 有人进入 ROI 即变红报警
4. 右侧勾选/取消类别可实时筛选
