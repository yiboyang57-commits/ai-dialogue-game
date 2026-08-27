# AI 对话模拟器 · Game Master 原型（DeepSeek）

一个命令行文字冒险游戏：你设定世界观与角色，模型扮演主持人（Game Master）推进剧情。
本原型重点演示「结构化状态管理 + tool calling 更新 + 历史压缩 + 本地持久化」，
**不依赖把全部历史对话堆给模型来维持一致性**。

## 快速开始

**最简单：双击启动器（点开就能玩）**

- macOS：双击 **`启动游戏.command`**（首次会装环境，之后自动启动并打开浏览器）
- Windows：双击 **`启动游戏.bat`**

**命令行版**：

```bash
cd /path/to/dsh-test
python3 main.py
```

**精致 Web 界面版**（Streamlit，三区布局 + 聊天气泡 + 卡片化状态面板）：

```bash
pip install -r requirements.txt   # 仅需 streamlit
streamlit run app.py
```

**图形界面版**（tkinter，零额外依赖）：

```bash
python3 gui.py
```

首次运行会引导输入 DeepSeek API Key（保存在 `config.json`）；
也可用环境变量提供：

```bash
export DEEPSEEK_API_KEY="sk-..."
python3 main.py   # 或 python3 gui.py
```

> 核心游戏逻辑仅用 Python 标准库（`urllib` 直连 `api.deepseek.com` 的 OpenAI 兼容接口）；仅 Web 界面额外需要 `streamlit`。

开始新游戏前，可选择世界观来源：

| 选项 | 说明 |
|------|------|
| `1` | **AI 自定义生成世界观**：输入主题/关键词/想扮演的角色，由 AI 通过 `setup_world` 工具生成结构化世界观，可预览、重新生成 |
| `2` | **上传自己的世界观文件**：JSON 直接解析；`.txt/.md` 文本交给 AI 整理成结构化设定 |
| `3` | 手动逐项输入 |

选定世界观后，还会进入 **主角构建** 步骤，可自选 **天赋**（0~3 个）、**体质**（单选）与
**金手指**（是否自带外挂，单选），详见下方「主角构建」章节。

游戏内命令：

| 命令 | 说明 |
|------|------|
| `/state` | 查看当前世界状态摘要 |
| `/rules` | 查看主持人限制条件 |
| `/save`  | 立即保存 |
| `/quit`  | 保存并退出 |
| `/help`  | 帮助 |

## 架构设计（对应你的核心技术要求）

### 1. 结构化状态管理 —— `world_state.py`
世界状态保存在 `save/state.json`，包含：世界背景、玩家（属性/状态效果）、当前地点、
时间、已知角色、背包、剧情标记（`plot_flags`）、当前目标、故事摘要、事件日志。
一致性靠这份 JSON，而不是完整对话历史。

### 2. 每轮流程 —— `game.py` + `tools.py`
每轮只发送：**当前状态摘要（由 JSON 实时生成）+ 最近几轮对话 + 玩家最新输入**。

模型输出两部分：
1. 给玩家看的叙事文本；
2. 通过 **function calling** 显式调用 `update_world_state(...)` 工具，返回结构化状态增量。

`apply_update` 用「增量合并」策略把增量写回 JSON（只改模型明确给出的字段，其余保留）。
如果模型偶尔忘记调用工具，会自动重试一次；仍失败则兜底记录一条事件。

### 3. 历史压缩 —— `memory.py` + `game.compress()`
- `recent` 只保留最近 3 轮完整对话（`KEEP_RECENT_ENTRIES = 6` 条）。
- 每累计 `COMPRESS_EVERY_TURNS = 20` 轮，把这段时间的对话交给模型压缩，同时产出
  「本章摘要」（滚动保留最近 `MAX_CHAPTER_SUMMARIES` 段）与「全局摘要」（`narrative_summary`）。
- 上下文因此被约束为：状态摘要 + 章节摘要 + 全局摘要 + 最近几轮；角色/背包/标记在摘要中截断，
  即使长局也不会无限增长。

### 4. 战斗判定 —— `combat.py`（隐藏计算，不显性展示）
- 战力体系在世界生成时锚定（`combat`：量纲 `field`、主角战力 `player_power`、段位差 `realm_gap`、曲线 `curve`）。
- 战斗/逃跑由**代码掷骰**判定（sigmoid 曲线：段位越高，跨段位翻盘概率越低），
  模型只负责叙述与记录，**不得自行决定结局**；概率数值与公式不发给玩家、也不发给模型。
- 战斗回合为「两次调用」：第一次声明敌人，系统判定后第二次叙述结果并记录战后状态。

### 5. 持久化 —— `save/` 目录
- `save/state.json`：世界状态（含战力锚定、章节/全局摘要、事件日志、最近战斗）。
- `save/history.json`：近期对话窗口 + 待压缩缓冲。
- 下次运行检测到存档会询问是否继续（读档恢复）。

### 6. 多存档隔离 + 网页版导入导出 —— `save_bundle.py`

- **多存档隔离**：`WorldState` / `Memory` 支持 `save_key`（存档名）。填了存档名后，
  状态/记忆会写到 `save/state_<存档名>.json`、`save/history_<存档名>.json`，不同玩家互不覆盖；
  留空则沿用原来的默认槽 `state.json` / `history.json`（CLI、GUI 不受影响）。
- **网页版导出/导入**：Streamlit 云端磁盘是临时的，因此网页版在侧边栏提供
  「导出存档」（把世界状态 + 对话历史打成 JSON 下载到自己设备）与「导入存档」（读回恢复进度），
  由 `save_bundle.dump_bundle / load_bundle` 实现，跨设备/跨重启也能继续玩。
- 网页版玩法：每人先在侧边栏填一个**不同的存档名**（如 `alice` / `bob`），再开始游戏；
  「继续存档」只读自己那个存档名下的档。

## 世界观模板（上传 / 生成统一格式）

三种来源（AI 生成、文件上传、手动输入）最终都归一化为同一份「世界观模板」，
字段与状态结构一一对应。上传 JSON 可参考 [`world.example.json`](world.example.json)：

```jsonc
{
  "world":  {"name": "...", "background": "...", "rules": "...", "style": "..."},
  "player": {"name": "...", "role_description": "...", "attributes": {"hp": 100}},
  "initial_situation": "...",
  "location": {"name": "...", "description": "..."},
  "time": "...",
  "characters": {"角色名": {"description": "...", "attitude_toward_player": "...", "location": "...", "status": "..."}},
  "inventory": [{"name": "...", "quantity": 1, "description": "..."}],
  "plot_flags": {"标记": true},
  "current_goal": "...",
  "combat": {"field": "修仙境界", "player_power": 10, "realm_gap": 5, "curve": "realm_gap"}
}
```

只有 `world.background` 或 `initial_situation` 两者至少其一为必填，其余可省略。

`world.style` 是**主持风格**（语气/文风/节奏），与世界观绑定：AI 生成时一并产出匹配的文风；
上传 JSON 可自带；留空则由主持人根据背景自动匹配。系统提示不再强制固定篇幅/句式。

`combat` 锚定战力体系（可省略则用默认值）；货币与道具一样放在 `inventory`，
用 `quantity` 表示数量，例如 `{"name": "金币", "quantity": 50}`。

## 主角构建 —— `character_builder.py`

选定世界观后、正式开局前，会让玩家构建主角基本信息，三类选项都定义在 `character_builder.py`：

| 类别 | 选择方式 | 效果 |
|------|---------|------|
| **天赋** | 多选 0~3 个（如「天命之子」「神机妙算」「炼器天才」…） | 名称对应 `judgment.TALENT_MODIFIERS` 的系数，影响各类判定成功率 |
| **体质** | 单选（如「均衡之躯」「先天道体」「纯阳之体」「霸绝武体」…） | 可带来属性（如生命上限）、初始战力或判定加成，写入 `player.physique` |
| **金手指** | 单选（「无」/「随身系统」/「天机推演」/「存档读档」/「天命气运」/「点石成金」） | 主角独有的外挂/优势，写入 `player.golden_finger`，带来全局或分类型判定加成 |

构建结果通过 `build_player()` 合并进世界观模板（覆盖模板自带的天赋），最终落到状态里的
`player.talents` / `player.physique` / `player.golden_finger`；体质与金手指同样参与判定（`game._talent_names`
会把三者一起交给判定引擎），并注入系统提示让主持人合理体现，但**不会写出数值**。
可选项、系数都集中在一处，想增删改只需编辑 `character_builder.py` 与 `judgment.py` 的 `TALENT_MODIFIERS`。

## 背包按数量增减（货币友好）

`update_world_state` 的 `inventory_add` 累加数量、`inventory_remove` 按 `quantity` 扣减，
**不再整类删除**（仅显式 `remove_all: true` 才会彻底丢弃某物，货币不要用它）。
因此货币（金币/灵石/元宝…）就是普通的数量型道具。

## 主持人限制条件 —— `gm_rules.json`

主持人必须遵守的限制独立于世界观，放在可编辑的 `gm_rules.json`（首次运行自动生成默认值）。
例如：内容分级、不替玩家做决定、不机械降神、不无端跳时间、失败/死亡要公平等。
直接编辑该文件的 `rules` 数组即可增删；游戏内用 `/rules` 查看当前生效的规则。

## 概率判定引擎 —— `judgment.py`（拟合因素 + 天赋/道具系数 + 大保底）

亲密关系、寻宝、说服、潜行、洞察、炼制、危险尝试等**成败不由主持人随意决定**，而是由代码把
「影响因素」拟合成概率、掷骰判定（对玩家隐藏）。判定**不写死**：所有权重/系数都在 `judgment.py` 的表中，可增删。

- 概率模型：`logit = base + Σ(因素权重 × 因素值) + 天赋系数 + 道具系数`，`p = sigmoid(logit)`。
  因素值由主持人声明为 `-3~+3` 整数（很不利 ~ 很有利）。
- 亲密关系默认 `base` 极低（刚见面建立亲密关系几乎不可能），并结合性格/场景/家庭背景/身份/好感度等因素。
- **天赋**（如「天命之子」）作为全局或分类型 logit 系数叠加，大幅提升成功率；开局天赋在 `player.talents` 锚定。
- **体质 / 金手指**（主角构建自选）同样以名字挂进 `TALENT_MODIFIERS` 参与判定，与天赋一起由 `game._talent_names` 收集。
- **一次性法宝/特殊道具**（如「护身符」「爆元丹」）在战斗/判定时按名查找系数，使用后消耗；常驻道具（如「寻宝罗盘」）被动加成。
- **大保底**：同一类判定连续失败达到阈值后强制成功；保底计数持久化在 `state.pity`，读档继续累计。

战斗判定（`combat.py`）同样接入天赋与一次性法宝系数。

## 去数据化（角色不显性展示数值）

内部数值（hp/战力/好感度/概率/伤害等）只存在 `save/state.json` 供系统与模型使用，
玩家可见的叙事与 `/state` 均用**定性语言**（如「受伤」「好感=高」「气色尚可」），不暴露具体数字。

## 世界观模板柔性化（不写死排版格式）

上传的 JSON 不强套固定模板：已知字段（world/player/combat/talents/…）会被识别并使用，
**未知字段原样保留进 `custom`**，并注入系统提示让主持人遵循；只有玩家未自定义时，系统才按 AI 生成的结构自由决定。

## 动态系数 —— `dynamics.py`（好感度等随事件/时间增减）

- 好感度 = 基础值 + 相识时长加成（每 5 回合 +1、封顶 +15）+ 关系事件增减。
- 关系事件：`RELATION_EVENTS` 表（救命之恩/并肩作战/赠礼/冒犯/背弃…），模型在
  `relationship_events` 写 `{subject, event}`，系统自动换算并钳制 0~100。
- 其它系数：`attribute_events`（声望/魅力/福缘/体力/修为…）按事件或显式 delta 增减。

## 每轮可选行动（帮助游玩）

模型每轮在 `options` 给出 2~4 个**显而易见**的下一步行动，CLI 用 `1) 2) 3)` 展示，
玩家输入数字即可选择，也可输入自定义行动；选项不含隐藏线索/隐藏剧情触发。

## 文件说明

| 文件 | 职责 |
|------|------|
| `main.py` | CLI 入口、世界观来源选择、主角构建、主循环 |
| `character_builder.py` | 主角构建：天赋/体质/金手指目录与合并逻辑 |
| `world_gen.py` | AI 生成 / 文件上传 / 模板归一化与预览 |
| `game.py` | 每轮流程编排、战斗/非战斗判定衔接、压缩触发、存读档 |
| `judgment.py` | 通用概率判定引擎（因素拟合 + 天赋/道具系数 + 大保底） |
| `dynamics.py` | 动态系数（好感度随时间/事件增减、属性事件表） |
| `combat.py` | 战斗/逃跑的隐藏概率计算（胜负判定，含天赋/法宝修正） |
| `world_state.py` | 状态 JSON 读写、去数据化摘要、模板应用、增量合并 |
| `save_bundle.py` | 网页版存档导出/导入（世界状态 + 对话历史打包） |
| `tools.py` | `update_world_state` 工具的 JSON Schema |
| `memory.py` | 近期窗口 + 压缩缓冲管理 |
| `prompts.py` | 系统提示（含风格/限制条件）、每轮上下文、压缩提示 |
| `gm_rules.py` / `gm_rules.json` | 主持人限制条件的加载与默认值 |
| `llm_client.py` | DeepSeek 客户端（urllib，含 tool calling 解析） |
| `app.py` | 精致 Web 界面（Streamlit，三区布局 + 卡片化状态面板） |
| `gui.py` | 图形界面（tkinter，复用核心逻辑） |
| `requirements.txt` | Web 界面依赖（仅 streamlit） |
| `game.spec` | PyInstaller 打包配置 |
| `config.py` | API Key / 模型 / 参数配置 |
| `world.example.json` | 世界观模板示例（可用于上传测试） |

## 打包为可执行文件（PyInstaller）

> ⚠️ 平台限制：PyInstaller 只能**在目标系统上**打包 —— Windows 上才能打出 `.exe`，macOS 上打出 `.app`。
> 当前仓库在 macOS 上，故不能直接产出 Windows `.exe`；请在 Windows 机器上执行下面命令。

```bash
pip install pyinstaller

# macOS → 一键打包 dist/AI对话模拟器.app（单文件自包含、双击即用）
python3 make_icon.py     # 可选：生成自定义图标 icon.icns
./build_macos.sh

# Windows → dist/AI对话模拟器.exe（无控制台；需在 Windows 机器上执行）
pyinstaller game.spec
```

> macOS 采用**单文件自包含**打包（`.app` 内部无软链），可避免桌面 iCloud 同步把 bundle 内
> 软链（如 `base_library.zip`）破坏导致运行时报错。CA 证书已随程序内置（`cacert.pem`），
> 不依赖系统 / anaconda 的证书路径。

打包后：`config.json`（API Key）与 `save/`（存档）会生成在**可执行文件同级目录**（代码已处理 PyInstaller 的路径差异）。
首次运行仍会在图形界面里弹窗让你输入 API Key。

## 可调参数（`config.py`）

- `KEEP_RECENT_ENTRIES`：保留的最近对话条数（默认 6，即 3 轮）。
- `COMPRESS_EVERY_TURNS`：多少轮压缩一次（默认 20）。
- `MAX_EVENT_LOG`：事件日志条数上限（默认 15）。
- `MAX_CHAPTER_SUMMARIES`：章节摘要保留条数（默认 6）。
- `DEFAULT_MODEL`：模型名（默认 `deepseek-chat`）。

## 注意事项

- 战斗曲线拟合参数在 `combat.py` 的 `CURVES` 中，可自行调；判定过程对玩家隐藏。
- 非流式输出：每轮会短暂显示「主持人思考中…」后一次性输出（战斗回合会多一次判定+叙述，稍慢）。
