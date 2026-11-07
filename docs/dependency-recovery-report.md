# 运行环境证据（不是锁文件）

原项目的运行器虚拟环境为 Windows Python 3.14.0，`include-system-site-packages=false`。其 `Lib/site-packages` 中的 dist-info 目录对应：MaaAgentBinary 1.0.1、MaaFw 5.14.2、NumPy 2.5.4、opencv-python 5.0.0.93、Pillow 12.3.0、pip 25.2、StrEnum 0.4.15。这些直接目录记录与根 requirements 的版本一致；并不代表已在干净电脑上安装成功。

面板虚拟环境为 Windows Python 3.12.10，`include-system-site-packages=true`。其**本地** dist-info 目录包含：adbutils 2.12.0、certifi 2026.10.23、charset-normalizer 3.5.2、comtypes 1.4.16、darkdetect 0.8.0、decorator 5.3.1、deprecation 2.1.0、idna 3.20、mouse 0.7.1、Pillow 12.3.0、pip 25.0.1、polib 1.2.0、psutil 7.2.2、pyappify 1.0.13、pycaw 20260927、PyDirectInput 1.0.4、pynput 1.8.2、PySide6-Fluent-Widgets 1.11.3、PySideSix-Frameless-Window 0.8.2、pywin32 312、requests 2.34.2、retry2 0.9.5、six 1.17.0 和 urllib3 2.8.0。

面板可继承基础 Python 的站点包，所以上述本地目录清单不是完整环境锁。只读包元数据显示被继承的基础环境有 NumPy 2.5.4、PySide6 Essentials 6.12.0；面板上游 requirements 则指定 NumPy 2.5.2、PySide6 Essentials 6.11.1。该 requirements 是上游模板记录，不代表本机完整锁。未发现可将 vendor 对应到具体发行包的 ok-script dist-info。

现有隔离探针仅表明：面板 Python 可导入 NumPy/Pillow/PySide6；单独面板环境缺 cv2/maa，原 bootstrap 追加运行器 site-packages 后 cv2 可导入、maa 可发现。运行器 Python 可导入 NumPy/OpenCV/Pillow。版本元数据与现有机器导入结果不能代替干净安装、跨 Python ABI 或完整依赖解决验证。

根目录 `config.json` 为用户本机配置，不应公开。可参照 `config.example.json` 的字段形状手动建立，但其中占位值不构成可运行配置。真实安装还须补齐并核对素材、上游授权及相应组件，建立两个环境并验证依赖解析、空用户数据与安全入口；不建议未经验证将两份 requirements 合并。
