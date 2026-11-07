# OK-SZB — 《影之诗：超凡世界》视觉识别与对局自动化研究工具

**OK-SZB** 是一款面向《影之诗：超凡世界》（*Shadowverse: Worlds Beyond*）的本地计算机视觉识别与卡牌对局策略研究项目。项目采用 **MaaFramework Win32 窗口捕获 + MuMu 模拟器原生 SDK 后台输入**方案（无需开启 ADB 调试，支持模拟器窗口后台运行），并提供 **PySide6 原生桌面控制面板**与**轻量级本地 Web 管理面板**两种交互方式。

---

## ✨ 功能特性

- **多模式自动化支持**
  - 🏆 **阶位积分战（Ranked）**：支持单局执行、指定目标场次批量连续对局、异常断点恢复与周期性休息调度。
  - 🎖️ **大奖赛（Grand Prix）**：支持大奖赛轮次自动进场与对局执行。
  - 🎪 **周末广场（Weekend Plaza）**：支持周末广场活动批量对局。
  - 🏝️ **乐园日常（Paradise Quests）**：支持乐园区域交互与每日任务流程。
  - 🤖 **单人练习赛（Practice CPU）**：支持针对指定职业 CPU 的本地策略测试与验证。
- **双端可视化控制面板**
  - **Qt 原生桌面客户端（`ok_panel`）**：基于 `PySide6-Fluent-Widgets` 构建，支持模拟器窗口绑定、各模式任务一键启停、内置卡牌图鉴浏览与自定义策略规则管理。
  - **轻量 Web 管理面板（`panel_app.py`）**：零额外 GUI 库依赖，启动后通过本地浏览器即可管理卡组、查看 595+ 张完整卡牌图鉴、调整出牌规则并控制运行器。
- **纯本地视觉与决策引擎**
  - 内置 PP-OCRv5 中文识别模型（`assets/resource/model/ocr/`）、114 张卡牌图像特征模板与 22 个核心对局场景参考图。
  - 包含完整的起手留换牌（Mulligan）、费用曲线规划、进化/超进化时机判断、弃牌收益评估、护盾处理与斩杀计算逻辑（见 `swb/policy.py` 与 `assets/strategy.json`）。

---

## 🖥️ 环境要求

1. **操作系统**：Windows 10 / Windows 11（64 位）
2. **模拟器**：**MuMu 模拟器 12**
   - 模拟器显示分辨率请设置为 **`1280 × 720`**（横屏，推荐 DPI `240`）。
3. **Python 版本**：
   - 核心对局运行器环境（`.venv`）：推荐 **Python 3.12+**
   - 原生 Qt 桌面面板环境（`.panel-runtime`，可选）：推荐 **Python 3.12**

---

## 🚀 安装与配置指南

### 1. 克隆仓库

```powershell
git clone https://github.com/miroyukiw/OK-SZB.git
cd OK-SZB
```

### 2. 安装 Python 依赖

#### （必选）创建核心运行器虚拟环境 `.venv`

在项目根目录下打开 PowerShell，执行以下命令：

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

#### （可选）创建原生 Qt 桌面面板环境 `.panel-runtime`

如果你希望使用 `start-panel.cmd` 启动 Fluent 风格的原生桌面客户端，请使用 Python 3.12 创建 `.panel-runtime` 并安装面板依赖：

```powershell
py -3.12 -m venv .panel-runtime
.\.panel-runtime\Scripts\pip install -r ok_panel/UPSTREAM_REQUIREMENTS.txt
```

> 💡 **提示**：如果你不想配置第二个虚拟环境，也可以直接使用 `.venv` 启动内置的 **Web 管理面板（`panel_app.py`）**，功能完全相同。

### 3. 配置 `config.json`

将 `docs/config.example.json` 复制到项目根目录并重命名为 `config.json`：

```powershell
Copy-Item docs/config.example.json config.json
```

打开根目录下的 `config.json`，根据你本机的 **MuMu 模拟器 12** 实际安装路径填写配置。示例如下：

```json
{
  "platform": "mumu",
  "cli": "D:\\Program Files\\Netease\\MuMuPlayer-12.0\\shell\\MuMuManager.exe",
  "instance": 0,
  "install_root": "D:\\Program Files\\Netease\\MuMuPlayer-12.0",
  "sdk": "D:\\Program Files\\Netease\\MuMuPlayer-12.0\\shell\\sdk\\external_renderer_ipc.dll",
  "input": "mumu-sdk",
  "cpu_pick": [640, 360],
  "cpu_label": "龙族",
  "target_order": ["leader", "ward", "follower"]
}
```

#### 配置字段说明

| 字段名 | 说明 | 示例值 |
| :--- | :--- | :--- |
| `platform` | 模拟器平台标识，固定为 `"mumu"` | `"mumu"` |
| `cli` | MuMu 模拟器命令行管理工具 `MuMuManager.exe` 的绝对路径 | `"D:\\...\\shell\\MuMuManager.exe"` |
| `instance` | MuMu 多开器中的模拟器实例索引（单开默认填 `0`） | `0` |
| `install_root` | MuMu 模拟器安装根目录 | `"D:\\...\\MuMuPlayer-12.0"` |
| `sdk` | MuMu 后台渲染通信组件 `external_renderer_ipc.dll` 的绝对路径 | `"D:\\...\\shell\\sdk\\external_renderer_ipc.dll"` |
| `input` | 输入驱动方式，使用 MuMu 原生后台输入请填 `"mumu-sdk"` | `"mumu-sdk"` |
| `cpu_pick` | 单人练习赛选择对手时的点击坐标 `[x, y]`（基于 1280×720） | `[640, 360]` |
| `cpu_label` | 单人练习赛目标对手标签名称 | `"龙族"` |
| `target_order` | 默认攻击目标优先级列表 | `["leader", "ward", "follower"]` |

---

## 🎮 快速开始（使用方法）

启动工具前，请先打开 **MuMu 模拟器 12** 并进入《影之诗：超凡世界》游戏主界面。

### 方式一：使用 Qt 原生桌面客户端（推荐）

双击运行根目录下的 **`start-panel.cmd`**，或在 PowerShell 中执行：

```powershell
.\start-panel.cmd
```

1. **绑定窗口**：在面板的「窗口捕获」页面检查或选择当前正在运行的 MuMu 模拟器窗口。
2. **启动任务**：在左侧导航栏切换至 **阶位积分战 / 大奖赛 / 周末广场 / 乐园任务**，设置计划对局场次后点击「开始」。
3. **浏览图鉴与规则**：在「卡牌图鉴」与「策略规则」页面可按职业/费用检索卡牌、配置自定义出牌优先级。

### 方式二：使用本地 Web 管理面板（轻量免安装 PySide6）

在 PowerShell 中使用 `.venv` 直接启动本地 Web 控制台：

```powershell
.\.venv\Scripts\python.exe panel_app.py
```

启动后浏览器会自动打开 `http://127.0.0.1:18765/`。常用可选参数：
- `--port 18765`：指定本地监听端口（默认 `18765`）
- `--no-browser`：启动服务但不自动弹出浏览器窗口
- `--offline`：以离线只读模式启动面板

### 方式三：使用命令行直接运行

- **启动单局练习赛（Practice CPU）**：
  ```powershell
  .\start.ps1
  ```
- **安全停止当前运行中的任务**（会在根目录生成 `STOP` 信号文件，当前动作完成后安全退出）：
  ```powershell
  .\stop.ps1
  ```
  > ⚠️ **注意**：执行 `.\stop.ps1` 后，根目录会保留 `STOP` 文件以防止误触发新对局。下次重新启动前，请先删除根目录下的 `STOP` 文件（`Remove-Item .\STOP`）。
- **启动阶位积分战（单局或批量）**：
  ```powershell
  # 查看当前代码与资源快照版本号
  $ver = (.\.venv\Scripts\python.exe -c "import run; print(run.version()['sha256'])").Trim()

  # 运行单局阶位积分战
  .\.venv\Scripts\python.exe ranked_once.py --expect-version $ver

  # 批量运行阶位积分战（例如创建名为 session1 的批次并执行 10 场）
  .\.venv\Scripts\python.exe ranked_once.py --expect-version $ver --batch session1 --matches 10
  ```
- **列出当前桌面可捕获窗口**：
  ```powershell
  .\.venv\Scripts\python.exe run.py --windows
  ```

---

## 🃏 内置策略卡组：疾驰弃牌龙（`dragon_tempo`）

当前默认策略路由固定针对轮换制 **疾驰弃牌龙（40 张：33 张随从 / 7 张法术）** 进行决策优化。开始自动对局前，请在游戏内导入以下卡组并设为默认出战牌组：

- 🔗 **[点击打开官网卡组分享链接](https://shadowverse-wb.com/chs/deck/detail/?hash=1.4.ebxM.ebxM.ebxM.eb-U.eb-U.ecgE.ecgE.ecgE.ecgO.ecgO.ecgO.e-M0.e-M0.e-M0.fN08.fN08.fN08.flAs.flAs.flAs.flD-.flD-.flD-.flQU.flQU.flQU.flg6.flg6.flg6.fljE.fljE.flvk.flvk.flvk.g7r8.g7r8.g7r8.g8KO.g8KO.g8KO)**

| 卡牌编号 (`card_id`) | 卡牌名称 | 投入数量 |
| :--- | :--- | :---: |
| `10941110` | 碎裂的盗匪 | 3 |
| `10941310` | 幼龙闹脾气 | 3 |
| `10644120` | 古旧天刀·波菈莱 | 3 |
| `10842120` | 满面笑容的烹饪·琪米卡 | 3 |
| `10942110` | 绝倒的袭击者 | 3 |
| `10943310` | 利牙 | 2 |
| `10641110` | 决断的龙人 | 3 |
| `11042120` | 侦察龙人 | 3 |
| `10741120` | 载运飞龙 | 3 |
| `10641310` | 天刀授予 | 2 |
| `10943110` | 尘土的不法者 | 3 |
| `11044120` | 龙惧·吉尔沃 | 3 |
| `10644110` | 断头的斩姬·相枛津 | 3 |
| `10944110` | 穿孔的罪人·安缇马丽亚 | 3 |

---

## 📂 项目目录结构

```text
OK-SZB/
├── swb/                        # 核心视觉识别、策略决策、传输控制与各模式状态机
│   ├── vision.py               # 场景 ROI 匹配、卡牌识别与 PP-OCRv5 推理封装
│   ├── policy.py               # 留换牌、出牌顺序、进化、弃牌与攻击目标决策逻辑
│   ├── transport.py            # MaaFramework Win32 截图与 MuMu SDK 后台点击/滑动驱动
│   ├── ranked.py / ranked_batch.py       # 阶位积分战导航与批量调度
│   ├── grand_prix.py / weekend_plaza.py  # 大奖赛与周末广场模式自动化
│   └── paradise.py / paradise_quests.py  # 乐园日常任务自动化
├── ok_panel/                   # PySide6 原生桌面控制面板（基于 ok-script 桌面模板）
├── panel/                      # 本地轻量 Web 管理面板前端页面与接口定义
├── assets/                     # 策略配置（strategy.json）、场景参考图（scenes/）、卡面模板与 OCR 模型
├── external/
│   ├── card-library/           # 本地只读 SQLite 卡牌数据库（cards.sqlite，含 595+ 张卡牌与别名）
│   └── card-images/            # 卡牌高清立绘与进化形态图库（1,016 张 PNG 及索引）
├── docs/                       # 配置文件示例（config.example.json）与归档说明文档
├── run.py                      # 对局核心运行器主入口
├── ranked_once.py              # 阶位积分战单局/批量安全启动器
├── panel_app.py                # 本地 Web 管理面板服务端入口
├── ok_panel_bootstrap.py       # Qt 原生桌面客户端引导入口
├── start-panel.cmd / .ps1      # 启动 Qt 桌面控制面板快捷脚本
├── start.ps1                   # 启动单人练习赛快捷脚本
└── stop.ps1                    # 发送 STOP 停止信号快捷脚本
```

---

## ❓ 常见问题排查（FAQ）

1. **启动时报错 `STOP 文件存在` 或 `STOP exists`？**
   - 这是上一次对局结束或手动执行 `stop.ps1` 后留下的安全锁文件。确认游戏画面处于正常状态后，删除项目根目录下的 `STOP` 文件即可重新启动。
2. **报错 `Another runner owns the window` 或 `RUNNING.lock` / `BATCH.lock` 已存在？**
   - 说明已有运行器进程在后台运行，或上次进程被强制关闭未清理锁文件。确认无后台 `python.exe` 运行后，删除根目录下的 `RUNNING.lock` 或 `BATCH.lock` 即可。
3. **报错 `MuMu instance is not running`？**
   - 请检查 `config.json` 中的 `cli` 路径是否正确，以及 `instance` 编号是否与当前打开的 MuMu 模拟器实例编号一致（可在命令行运行 `MuMuManager.exe info --vmindex 0` 自检）。
4. **视觉识别不到按钮或手牌？**
   - 请确认 MuMu 模拟器分辨率已严格设置为 **`1280 × 720`**，且游戏内界面语言为**简体中文**。

---

## 📜 来源致谢与许可说明

- **桌面面板框架**：`ok_panel/` 基于或参考了 [ok-script](https://github.com/ok-oldking/ok-script) 及 `ok-script-app` 模板构建，界面组件使用 `PySide6-Fluent-Widgets`。相关开源许可原文保留于 `ok_panel/` 及 `THIRD_PARTY_LICENSES/` 目录。
- **底层图像捕获与 OCR 模型**：窗口截图绑定使用 [MaaFramework](https://github.com/MaaXYZ/MaaFramework)，资源目录结构参考 [MaaPracticeBoilerplate](https://github.com/MaaXYZ/MaaPracticeBoilerplate)（MIT License）。`assets/resource/model/ocr/` 下的中文字体检测与识别模型源自 [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets)（MIT License）及 PaddlePaddle [PP-OCRv5](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_rec)（Apache-2.0 License）。
- **游戏素材声明**：仓库内包含的卡面图像、场景裁剪参考图及游戏文本的相关权利归属于 Cygames 及相关权利人，遵循其[服务条款](https://shadowverse-wb.com/en/terms/)与[衍生内容指引](https://shadowverse-wb.com/en/guideline/)，本项目不对此类第三方游戏素材授予额外许可。关于归档脱敏与文件范围的补充说明见 `PUBLIC_ARCHIVE_NOTES.md`。

本项目完全免费，仅供机器学习研究；如有问题，请通过 GitHub Issues 联系我删除。
