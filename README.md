# OK-SZB 公开备份候选

这是 OK-SZB 的源码与资源备份。它包含项目源码、面板 vendor、卡牌目录与图像索引、游戏识别资源和 OCR 模型；原用户配置、运行日志和个人状态未包含。11 张必需场景图的识别区域以外已遮罩，四张当前禁用分支的个人画面未收录，见 `docs/PRIVATE_IMAGE_EXCLUSIONS.md`。

## 缺失内容与依赖

本备份包含识别图像、卡面、OCR 模型及 vendor，不含本机设置或 Python 运行环境。正常场景配置的 22 张参考图均已收录；其中 11 张经隐私遮罩，识别 ROI 与原始像素逐一核对一致。四张当前禁用的 Nightmare 分支参考图未收录；重启该分支需另行准备安全参考图。

Python 依赖版本参考 `requirements.txt` 和 `ok_panel/UPSTREAM_REQUIREMENTS.txt`。这些文件是原项目的依赖记录，并非已经验证的安装指南；相关上游依赖及其各自许可仍需单独核对。

运行入口需要自行创建本机配置；面板依赖的卡片数据库及图像索引分别位于 `external/card-library/cards.sqlite` 和 `external/card-images/card_image_index.json`。两份依赖记录中的 NumPy 版本不同，尚未验证能否组合使用。

## 中文使用说明（恢复中）

### 安装与启动

使用前需在 Windows 上配置 Python 环境并按 `docs/config-example-NOTES.md` 填写本机配置。原环境记录显示运行器使用 Python 3.14 和 NumPy 2.5.4，面板使用 Python 3.12；面板上游依赖清单另写 NumPy 2.5.2。两份清单不是同一环境中经验证的安装锁，具体元数据与已测边界见 `docs/dependency-recovery-report.md`。

原脚本入口为：在 PowerShell 中运行 `./start-panel.cmd` 启动面板（依赖 `.panel-runtime/Scripts/pythonw.exe`），运行 `./start.ps1` 启动运行器（依赖 `.venv/Scripts/python.exe`），运行 `./stop.ps1` 创建 `STOP` 文件请求停止。**这些启动入口尚未通过完整验收，环境配置未解决前不能保证工作；面板默认不是离线模式。**

在私有隔离副本中，面板数据组件、策略读取和空状态卡组选择已完成检查；本候选的视觉组件已加载 114 张卡牌、22 个场景参考和 OCR 资源。Web 面板测试绑定本机回环地址后，首次读取页面被系统以 WinError 10013 拒绝，GUI、网页 API、游戏实战及干净环境安装均未验收。

源码中有排位、大奖赛、周末广场、乐园任务与卡牌页面入口，但入口存在不代表这些功能在此恢复副本中已测试或可用。

### 卡组与配置

当前原项目配置指定轮换制**疾驰弃牌龙**（`dragon_tempo`），40 张：33 张随从、7 张法术。原项目运行器的当前版本存在该龙系路径完成实战并到达结果画面的历史证据，下表来自同一资源快照，但记录并未逐张核验客户端当时的牌组。该证据不覆盖面板，也不证明本恢复副本已通过实战；游戏客户端当前选择的卡组是否与下表一致，需由使用者自行核对。

[官方可分享链接](https://shadowverse-wb.com/chs/deck/detail/?hash=1.4.ebxM.ebxM.ebxM.eb-U.eb-U.ecgE.ecgE.ecgE.ecgO.ecgO.ecgO.e-M0.e-M0.e-M0.fN08.fN08.fN08.flAs.flAs.flAs.flD-.flD-.flD-.flQU.flQU.flQU.flg6.flg6.flg6.fljE.fljE.flvk.flvk.flvk.g7r8.g7r8.g7r8.g8KO.g8KO.g8KO)：官方页面已核对 14 种、40 张，随从 33 张、法术 7 张、护符 0 张；未查到明确到期时间。页面核对不等于游戏实战验证。

| card_id | 卡牌 | 数量 |
| --- | --- | ---: |
| 10941110 | 碎裂的盗匪 | 3 |
| 10941310 | 幼龙闹脾气 | 3 |
| 10644120 | 古旧天刀·波菈莱 | 3 |
| 10842120 | 满面笑容的烹饪·琪米卡 | 3 |
| 10942110 | 绝倒的袭击者 | 3 |
| 10943310 | 利牙 | 2 |
| 10641110 | 决断的龙人 | 3 |
| 11042120 | 侦察龙人 | 3 |
| 10741120 | 载运飞龙 | 3 |
| 10641310 | 天刀授予 | 2 |
| 10943110 | 尘土的不法者 | 3 |
| 11044120 | 龙惧·吉尔沃 | 3 |
| 10644110 | 断头的斩姬·相枛津 | 3 |
| 10944110 | 穿孔的罪人·安缇马丽亚 | 3 |

当前源码的牌组路由固定返回 `dragon_tempo`；虽然代码中留有其他 profile 名称，但现有路由不支持切换，不能将其列为当前可用卡组。牌组配置文件的公开样例尚未完成；原项目的配置含个人标识，没有原样收录。恢复所需资源并验证样例后，才能给出可靠的配置步骤。

## 来源与许可

本项目使用或参考了 [ok-script](https://github.com/ok-oldking/ok-script)（面板来源记录为 OK-WW v3.7.4 的 ok-script）。原项目自带许可见 `ok_panel/LICENSE.ok-script.txt`，Apache 2.0 正文见 `ok_panel/LICENSE.Apache-2.0.txt`，并含 Commons Clause 及附加条款。上游不同版本的许可可能不同，vendor 文件与确切版本的对应关系仍在核实；不能将现有许可文件视为本项目全部组件的许可证。具体归属和本地可用许可原文见 `THIRD_PARTY_LICENSES/`。

上游 OK-WW v3.7.4 以 AGPL-3.0 发布，并记录依赖 ok-script 2.0.8；模板依赖记录的 2.0.0b7 在对应历史提交也采用 AGPL-3.0。尚未证明这些发行版本与本机 vendor 文件逐项一致。面板还依赖 GPLv3/商业双许可的 PySide6-Fluent-Widgets；`ok_panel/src/config.py` 标明改编自 ok-script-app 模板，但所用模板快照的许可尚未确认。

卡面、游戏图像及游戏文本的版权属于 Cygames 及相关权利人；本项目公开不为这些第三方素材授予新的许可。[游戏服务条款](https://shadowverse-wb.com/en/terms/)对内容复制和分发设有限制，[内容指引](https://shadowverse-wb.com/en/guideline/)对特定粉丝内容另有条件；其对各文件的适用情况需自行核对。

原项目记录其资源结构参考 [MaaPracticeBoilerplate](https://github.com/MaaXYZ/MaaPracticeBoilerplate)，其适用的 MIT 版权和许可文本保存在 `THIRD_PARTY_TEMPLATE_LICENSE`。原记录还列出 MaaFramework 和 MaaCommonAssets 的来源；除了下述 OCR 模型，本工作副本不包含这两个上游项目。

原项目未提供覆盖全部原创部分的根级许可证。本存档未替这些部分指定新许可证，也不声称仓库整体采用上述任一第三方许可证。有关存档范围和处理记录见 `PUBLIC_ARCHIVE_NOTES.md`。

`assets/resource/model/ocr/` 的四个文件与项目内记录的 MaaCommonAssets `OCR/ppocr_v5/zh_cn` 内容逐项哈希一致，其 MIT 许可随文件保留；相应 PaddlePaddle [检测模型](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det)及[识别模型](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_rec)模型卡声明 Apache 2.0。原始权重到本地 ONNX 的具体转换链未独立验证。
