"""页面层：每个业务功能一个独立页面文件，由 MainWindow 的左侧导航切换。

页面是纯视图层：
- 不持有 detector / worker 等核心服务
- 通过 MainWindow 注入的信号/回调与核心层通信
- 统一继承 BasePage，提供 title / set_palette 约定
"""
