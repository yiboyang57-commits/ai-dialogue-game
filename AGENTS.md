# AGENTS.md —— 万界 / AI 对话模拟器：Agent 上手指南

> **这份文件是给 AI Agent 看的，不是给人看的产品介绍。**
> 目标是让一个新开的对话在 **不读完全部源码** 的情况下，就能安全、正确地改这个项目。
> 详细功能说明看 [`README.md`](README.md)（309 行，按版本号堆叠，偏"功能清单"）；
> 本文件只讲 **架构、命令、铁律、改动地图**。

---

## 0. 这个项目是什么（60 秒版）

一个中文文字冒险游戏：玩家设定世界观+主角，**DeepSeek 模型扮演主持人（GM）**推进剧情。

```
玩家输入 → 模型叙事 + 调用 update_world_state 工具 → 结构化增量合并进 JSON → 存盘
```

**核心卖点也是核心架构约束**：一致性来自一份结构化 JSON 状态（`save/state.json`），
**不是**把全部历史对话堆给模型。所以上下文永远被约束为
`状态摘要 + 章节摘要 + 全局摘要 + 最近 3 轮`。

### ⚠️ 全项目最重要的一条设计原则

> **模型负责「声明」和「叙述」，代码负责「掷骰」和「结算」。**

模型**不得**自行决定：战斗胜负、判定成败、掉多少血、是否死亡、能拿到什么战利品。
它只能通过工具参数**声明**"这里要打一架 / 这里要判定一下"，然后由 `combat.py` / `judgment.py`
用代码掷骰，结果再回灌给模型让它叙述。

任何改动如果让模型重新获得了"自己决定结果"的能力，**就是架构退化**，应当拒绝。
`tests/test_gameplay.py` 里大量用例专门锁死这条边界（见 §6）。

---

## 1. 运行与测试（**已验证的命令，照抄即可**）

### 🔴 第一铁律：用项目自带的 venv，不要用系统 `python3`

系统 python 是 anaconda 的 3.13，里面装的是**老版 streamlit**（不支持 `st.container(autoscroll=...)`），
会直接导致 3 个网页测试失败。项目 venv 里是 **streamlit 1.62.0**，测试全绿。

```bash
# 目录：/Users/yangyibo/Desktop/万界
.venv/bin/python -m unittest tests.test_gameplay tests.test_character_pool tests.test_character_pool_ui tests.test_chat_ui
```

上面这条是**唯一可靠的全量测试命令**。基线：**132 passed, OK**（约 1~5s）。
改完代码**必须**跑它。

### ⚠️ 三个被验证过的测试陷阱（不要浪费时间去试）

| 命令 | 结果 | 说明 |
|------|------|------|
| `python3 -m unittest discover -s tests -t .` | ❌ `ImportError: Start directory is not importable` | `tests/` 没有 `__init__.py`，无法作为包发现 |
| `python3 -m unittest discover`（根目录） | ⚠️ **`Ran 0 tests`，静默假成功** | 最危险：看起来通过，其实一个都没跑 |
| `cd tests && python3 -m unittest` | ❌ `ModuleNotFoundError: No module named 'game'` | 根目录不在 `sys.path` |

`tests/*.py` 全部 `import combat` / `from game import Game` 这种**扁平导入**，
所以**必须在项目根目录**、用**显式模块路径**运行。pytest 未安装也不是依赖。

### 启动三个前端

```bash
# 网页版（Streamlit，主力界面，唯一使用 v2 候选池的界面）
.venv/bin/python -m streamlit run app.py

# 命令行版（纯标准库）
.venv/bin/python main.py        # 或 python3 main.py，无第三方依赖

# 图形版（tkinter，零额外依赖）
.venv/bin/python gui.py

# 双击启动器（macOS/Windows，自动建 venv + 装依赖 + 起服务 + 开浏览器）
./启动游戏.command        # macOS
启动游戏.bat              # Windows
```

> `启动游戏.command` 会检查 streamlit 版本是否满足 `>=1.62,<2`，不满足就重新 `pip install -r requirements.txt`。

### 依赖与 API Key

- `requirements.txt` 只有一行：`streamlit>=1.62,<2`。
  **核心游戏逻辑零第三方依赖**（纯 `urllib` 直连 `api.deepseek.com`），只有网页版需要 streamlit。
- API Key 解析顺序（`config.load_api_key()`）：**环境变量 `DEEPSEEK_API_KEY` → `config.json` 的 `api_key`**。
  网页版多一层：`app._secrets_api_key()`（Streamlit Cloud 的 `st.secrets`）优先于 `load_api_key()`，
  两者都没有才弹密码输入框并 `save_api_key()` 落盘。
- `config.json` 和 `save/` 都在 `.gitignore` 里，**不要提交**。
- ⚠️ **提醒用户**：`config.json` 当前含一个真实的 DeepSeek API Key。如果你把它打印到对话/日志里，
  提醒用户轮换该 Key。**任何时候不要把 Key 写进代码、文档、测试或 git。**

---

## 2. 架构与目录地图

### 分层（非常干净，没有循环依赖）

```
入口层   main.py (CLI)   gui.py (tkinter)   app.py (Streamlit)
              └──────────────┴──────────────┘
                             ↓
编排层   game.py            每轮流程 / 判定衔接 / 压缩 / 存读档 / 失败回滚
                             ↓
核心层   world_state.py  状态JSON读写+增量合并+摘要   memory.py  近期窗口+压缩缓冲
         judgment.py     概率判定引擎                combat.py  战斗判定曲线
         dynamics.py     好感度/属性动态系数          prompts.py  系统提示+每轮上下文
         seasons.py      岁月模式：按旬推进时间/掷随机事件（纯函数+数据表）
         tools.py        update_world_state 的 JSON Schema（并在底部重导出 apply_update）
         storage.py      SQLite 事件层（可选审计/快照/迁移；JSON 仍是主存储）
                             ↓
基础层   config.py (常量/路径/存档槽)   llm_client.py (urllib HTTP)   gm_rules.py
         character_builder.py (主角构建)   world_gen.py (世界观生成/归一化)   save_bundle.py
```

**入口层互不依赖，也不被核心层依赖** —— 可以放心改某个前端而不影响引擎。
`tools.py` 底部 `from world_state import apply_update` 只是**集中导出**（`# noqa: E402`），不是循环依赖。

### 文件职责速查

| 文件 | 行数 | 职责 |
|------|-----:|------|
| `game.py` | 775 | **编排核心**：每轮流程、战斗/判定两段式、压缩、失败回滚 |
| `world_state.py` | 823 | 状态 JSON 读写、`to_summary()`(给模型)、`to_player_summary()`(去数据化给玩家)、`apply_update` |
| `app.py` | 1454 | Streamlit 网页版（**唯一使用 v2 候选池 + 岁月模式界面**） |
| `gui.py` | 1153 | tkinter 图形版（**使用 legacy 固定目录**） |
| `character_builder.py` | 587 | 天赋/体质/金手指：legacy 目录 + v2 随机候选池（两套 API 并存） |
| `world_gen.py` | 485 | AI 生成 / 文件上传 / 手动输入 → 统一世界观模板 |
| `main.py` | 385 | CLI 入口与菜单 |
| `judgment.py` | 353 | 概率判定引擎（`logit = base + Σ因素 + 天赋 + 道具`, `p = sigmoid`） |
| `prompts.py` | 221 | 系统提示拼装 + 每轮消息 + 压缩提示（含【岁月模式】段） |
| `tools.py` | 240 | `update_world_state` 完整 JSON Schema（含 `time_block` / `offscreen`） |
| `combat.py` | 168 | 战斗/逃跑隐藏概率曲线 |
| `config.py` | 185 | 路径、常量、存档槽、Key 读写 |
| `llm_client.py` | 136 | urllib 直连 DeepSeek，tool calling 解析 |
| `memory.py` | 89 | `recent`(最近3轮) + `buffer`(待压缩) |
| `dynamics.py` | 80 | 好感度公式 + 关系/属性事件表 |
| `seasons.py` | 155 | **岁月模式**：按旬推进时间、掷小插曲/打断事件、回血与时长文案（纯函数 + 数据表） |
| `save_bundle.py` | 57 | 网页版存档导出/导入 |
| `gm_rules.py` / `gm_rules.json` | 51 | 主持人全局限制条件（与世界观解耦，可编辑） |
| `storage.py` | 254 | **路线图阶段 1**：SQLite 事件层（turns/state_changes/rolls/snapshots），可选双写 + 迁移 |
| `scripts/migrate_json_to_sqlite.py` | 52 | 把 JSON 存档迁移成 .db（`--slot` / `--all`） |
| `world.example.json` | — | 世界观模板示例 |
| `万界.txt` / `九天万界.docx` | — | **设计参考文档，不被任何代码引用**，不要当成依赖 |

---

## 3. 一轮游戏到底发生了什么（`game.py` 真实流程）

```
Game._play_turn(player_input, add_user)
 │
 ├─ 0. 快照 state.data + memory.to_dict()          ← 失败回滚的基线
 │
 ├─ 1. _generate()
 │     system = build_system_prompt(state, rules)   ← 每轮重建，保证战力/天赋最新
 │     messages = [system] + build_turn_messages(状态摘要 + 最近3轮 + 本次输入)
 │     text, update = _call_world_update(messages)
 │        └─ 若模型**没调工具**：追加一条"请只补上 update_world_state"重试一次
 │           仍失败 → raise LLMError（本轮未结算，可重试）
 │           ⚠️ 判断条件是 `not isinstance(update, dict)`：只有"完全没调工具"或
 │              "工具参数 JSON 损坏"（→ None）才重试；**合法但空的 `{}` 不重试**，
 │              按"本轮无状态变化"直接提交（apply_update 无操作，但 turn 照样 +1）。
 │
 ├─ 2. _handle_rolls()   ← 只有这里会触发两段式
 │     ├─ update["combat"]   → _handle_combat()
 │     └─ update["judgment"] → _handle_judgment()
 │     都没有 → 直接进第 3 步（普通轮，一次 API 调用）
 │
 │   【两段式判定轮】以战斗为例：
 │     a. _prepare_roll()  ← 只接受"判定前"的场景变化(location/time/角色描述)，
 │     │                      **丢弃**模型提前猜的 inventory_add / options / 胜负
 │     b. 代码掷骰 combat.resolve(...)  ← 胜率/伤害/是否死亡 全由代码算
 │     c. 结算：扣血、消耗一次性道具、消耗保命次数、hp<=0 则 mark_dead()
 │     d. combat.gm_brief() 生成**定性**简报（不含概率数值）发给模型
 │     e. _generate_outcome()  ← 第二次 API 调用，让模型叙述结果并记录战后增量
 │     f. merge_updates(第一次的context, 第二次的outcome) 合并两次增量
 │     g. _protect_hp()  ← **强制**把代码算出的 hp 写回，覆盖模型可能乱写的值
 │
 ├─ 3. _commit()
 │     apply_update(state, update)  →  bump_turn()  →  memory 追加
 │     → needs_compression() 则 _maybe_compress()（失败不丢本轮；连续失败 3 次后退避 5 轮）
 │     → save()
 │
 └─ except: 回滚 state.data 与 memory 到第 0 步快照，重新抛异常
             （磁盘未被写过，因为 save() 在第 3 步末尾）
```

### 关键语义（改动前必须理解）

- **`merge_updates(a, b)`**（`game.py:25`）：合并判定轮两次工具调用的增量。特例：
  - `characters` / `plot_flags` / `player_attributes` → **深合并**（保留未提及的键）
  - `inventory_add` / `inventory_remove` / `relationship_events` / `attribute_events` → **列表拼接**
  - `options` → **以第二次为准**（必须是结算之后的选项）
  - `turn_event` → 用 `；` 拼接
- **`_prepare_roll()`**（`game.py:124`）：判定前只允许场景变化落地。这是"模型不能提前发奖励"的实现手段。
- **`_protect_hp()`**（`game.py:139`）：把模型给的 `player_attributes.hp` 覆盖为代码值，
  并**剔除** `attribute_events` 里所有 `attribute == "hp"` 的条目。**HP 是系统独占字段。**
- **`_guard_vitals()`**（v2.18）：每轮提交前都会执行。**普通回合**（没有 combat/judgment）模型
  **不能扣血或致死**，只能回血，且回血按本轮之前的 `max_hp` 封顶；`max_hp` 只接受 >=1 的数字。
  受伤与死亡只能走 `combat` 或 `judgment.failure_consequence = injury|death`。
- **死亡由代码定，模型漏调工具也不能复活**（`game.py:355`）：`is_dead` 时只拿文本，
  `update` 缺失就补 `turn_event` 并强制 `options = []`。
- **`mark_dead()`** 会设置 `life_status="dead"`、`hp=0`、`status_effects=["死亡"]`、`options=[]`、
  `current_goal="本次人生已结束"`（`world_state.py:158`）。
- **`is_dead` 判定**：`life_status == "dead"` **或** `hp <= 0`（兼容把 hp 打到 0 的旧存档）。

### 失败回滚（很重要，别破坏）

`_play_turn` 用 `copy.deepcopy` 快照整个 state 和 memory，任何异常都回滚。
因此**第二次 API 调用失败时，未完成的扣血、道具消耗、保底计数全部撤销**，玩家可安全重试。
`save()` 只在 `_commit` 末尾调用，所以失败时**磁盘保持干净**。
`tests/test_gameplay.py::test_failed_second_call_rolls_back_items_hp_memory_and_disk` 锁死此行为。

---

## 4. 数据契约

### 4.1 `save/state.json` 结构

```jsonc
{
  "meta":   {"turn": 0, "created_at": "...", "updated_at": "...", "save_key": ""},
  "world":  {"name","background","rules","style"},        // style = 主持文风，与世界观绑定
  "player": {
    "name","role_description",
    "attributes": {},        // 内部数值：hp / max_hp / 境界... 不向玩家显性展示（max_hp 单轮变动钳制 0.5×~1.5×）
    "status_effects": [],    // ["受伤","疲惫"] —— 模型整表覆盖
    "talents": [],           // [{"name","description","tier","drawback"}]
    "physique": null,        // {"name","description","tier","drawback"} 单选
    "golden_fingers": [],    // 列表（可 1~3 个）；兼容旧字段 golden_finger（单数）
    "life_status": "alive",  // "alive" | "dead"
    "death_cause": "...",           // 仅死亡时存在
    "death_protections_used": {}    // {"古玉护佑": 1} 持久化，不靠模糊冷却回充
  },
  "initial_situation": "",
  "location": {"name","description"},
  "time": "",
  "characters": {},          // 角色名 -> {description, attitude_toward_player(文字), location, status, affinity(0~100 数字，首次出现按态度给初值), met_turn, memories[与玩家的往事，最多8条]}
  "inventory": [],           // [{"name","quantity","description"}] —— 货币也是数量型道具
  "plot_flags": {},
  "current_goal": "",
  "narrative_summary": "",   // 全局摘要（压缩产出）
  "chapter_summaries": [],   // 每 20 轮一段，滚动保留 MAX_CHAPTER_SUMMARIES=6
  "event_log": [],           // 一行事件，上限 MAX_EVENT_LOG=15
  "combat": {"field":"战力","player_power":10,"realm_gap":5,"curve":"realm_gap"},  // 战力锚定
  "last_combat": null,       // {enemy_name, enemy_power, outcome, enemy_defeated, player_hp_after}
  "pity": {},                // {判定类型: {"fails": n, "successes": n}} 保底计数，读档继续累计
  "custom": {},              // 上传世界观的未知字段，原样保留并注入系统提示
  "options": [],             // 本轮结束给玩家的 2~4 个"显而易见"选项
  "calendar": {"days": 0},   // 开局以来经过的天数（岁月模式按旬累加，只由代码写）
  "offscreen": [],           // 玩家不知道的幕后进展，最多 MAX_OFFSCREEN=10 条
  "pending_block": null,     // 被打断且还有剩余的时间安排 {plan, remaining, seclusion}
  "last_block": {...}        // 最近一次安排的定性结果 {plan, elapsed, interrupted}
}
```

**`save/history.json`**：`{"recent": [...], "buffer": [...], "turns_since_compression": n}`

### 4.2 存档槽命名（多存档隔离）

`save_slot_suffix(save_key)`：空 key → `""`（默认槽）；否则清洗为 `"_" + 只保留字母/数字/中文/-/_`。

| save_key | state 文件 | history 文件 |
|----------|-----------|--------------|
| `""`（默认） | `save/state.json` | `save/history.json` |
| `alice` | `save/state_alice.json` | `save/history_alice.json` |
| `临时-529768` | `save/state_临时-529768.json` | `save/history_临时-529768.json` |

`config.list_save_slots()` 扫描 `state_*.json` 返回 `[{"key","name","updated"}]`（不含默认槽）。

### 4.3 `update_world_state` 工具的全部字段（`tools.py`）

模型唯一的写状态入口。**未给出的字段保持原值**（增量合并）。

| 分类 | 字段 | 类型 | 备注 |
|------|------|------|------|
| 场景 | `location` / `location_description` / `time` | string | 时间不得无故跳天 |
| 场景 | `days_passed` | integer | 0~9：本轮实际经过的整天数，计入日历与年龄（P1-11）；≥10 天必须走 `time_block` |
| 玩家 | `player_attributes` | object | 只含变化的键；**`hp` 由代码保护覆盖** |
| 玩家 | `player_status_effects` | string[] | **完整覆盖**，不是增量 |
| 玩家 | `player_power` | number | 突破/升级时更新；**单次变化钳制 ±1 个 `realm_gap`**（P0-2；岁月结算按天数放宽到 `realm_gap×max(1,天数/180)`） |
| 角色 | `characters` | object | 按名 upsert，缺失字段保留；`affinity`/`met_turn`/`interactions` 是**系统独占**，模型写入被丢弃（P0-2），好感只能走 `relationship_events` |
| 背包 | `inventory_add` | array | 累加到同名条目；`quantity` 缺省或为 **0** 都会被 `or 1` 兜底成 1；无 `name` 的条目被丢弃 |
| 背包 | `inventory_remove` | array | 按 `quantity` **扣减**，`remove_all:true` 才整类删除（货币别用）。容错：裸字符串 `"护身符"` 等价于扣 1；最终 `quantity<=0` 的条目被整体过滤 |
| 剧情 | `plot_flags` / `current_goal` | object / string | 合并 |
| **判定** | `combat` | object | `enemy_name,enemy_power,action(fight\|flee)` **必填**；可选 `use_item,lethal` |
| **判定** | `judgment` | object | `type` **必填**；可选 `subject` / `factors` / `use_item` / `certain_failure` / `reason` / `failure_consequence`（枚举 `setback\|injury\|death`）/ `failure_severity`（`light\|medium\|heavy`，`injury` 时由代码按生命上限比例结算） |
| 动态 | `relationship_events` | array | `{subject, event}`，好感度由系统按事件表换算，**模型不写数值** |
| 动态 | `attribute_events` | array | `{event}` 或 `{attribute, delta}`；`hp`/`max_hp` 是系统独占，写入被忽略（审查 H1） |
| 输出 | `options` | string[] | 结算后的 2~4 个显性选项；死亡时必须为空 |
| 输出 | `turn_event` | string | 一句话本轮事件，进 `event_log` |
| **岁月** | `time_block` | object | `plan`/`days` **必填**，`seclusion` 可选。声明"一段时间做什么"，**由代码按旬推进并结算**；模型不得自己跳时间，也不得在本次回复里写出这段经过 |
| **岁月** | `offscreen` | string[] | 玩家不知道的幕后进展，每条一句。去重、保留最近 10 条；**只进 `to_summary()`，绝不进 `to_player_summary()`**。`time_block` 本身不在 `apply_update` 里处理（走 `game._resolve_block` 两段式）|

---

## 5. 判定与战斗的数值规则（**改数值前必读**）

### `judgment.py` —— 概率判定

```
logit = base + Σ(因素权重 × 因素值) + Σ(天赋/体质/金手指系数) + 道具系数
p     = sigmoid(logit)          # 钳制到 [0.001, 0.999]
```

- **因素值**：模型声明为 **-3 ~ +3 整数**（超出会被 `max(-3,min(3,...))` 钳制）。
  未知因素名给温和默认权重 `0.3`（允许自定义因素）。
- **因素堆叠上限**（P1-5）：未知因素合计贡献钳制在 ±1.5，全部因素合计钳制在 ±6 logit。
  **统一约定：因素值 = 对玩家的有利程度，所有权重必须为正**（测试锁死）。
  旧名「风险程度」「对方警觉度」通过 `FACTOR_ALIASES` 映射到「风险可控度」「对方松懈度」；
  因素名匹配会忽略 `/`、空格等（`factor_weight()`），所以「福缘运气」能匹配「福缘/运气」。
- **判定类型** `JUDGMENT_TYPES`（7 种）：`relationship`(base -3.0, pity 12)、`treasure`(-1.5, 8)、
  `persuasion`(-0.5, 6)、`insight`(0.0, 6)、`sneak`(-0.3, 6)、`craft`(-0.8, 8)、`action`(0.0, 8)。
  默认类型 `action`。亲密关系 base 极低是**有意设计**（刚见面几乎不可能）。
- **天赋系数** `TALENT_MODIFIERS`：`global` 作用于所有判定（含战斗）。
  内置：天命之子/福星高照/魅惑天成/巧舌如簧/神机妙算/身手敏捷/百战之躯/炼器天才/气运加身/倒霉体质
  **+ 体质**（均衡之躯/先天道体/纯阳之体/玄阴之体/霸绝武体/万毒不侵）
  **+ 金手指**（随身系统/天机推演/存档读档/天命气运/点石成金）。
- **自定义项**（玩家自己起的名字）：`infer_modifier(name)` 按 `CUSTOM_MODIFIER_KEYWORDS` 关键词命中累加，
  每类上限 `_MAX_CAT = 1.5`；完全命中不到就给 `{"global": 0.4}` 兜底。
- **等级倍率** `TIER_SCALE`：白 0.4 / 绿 0.7 / 蓝 **1.0(基准)** / 紫 1.3 / 金 1.7 / 红 2.2 / **炫彩 5.0**（bug 级）。
  最终 `(global + 该类) × TIER_SCALE`。
- **软上限** `TALENT_BONUS_CAP = 4.0`（v2.19）：天赋/体质/金手指加成之和被钳制在 ±4 logit，
  基础五五开的判定最高约 98%，堆再多高品级也不会变成必成功。
- **道具** `ITEM_MODIFIERS`：`consumable:true` 的用后扣 1；非消耗的（如寻宝罗盘）**被动常驻加成**。
  `combat_power` 加战力、`combat`/`flee` 加战斗 logit、`flee_guarantee` 逃跑必成功。
- **渐进保底**（P1-7）：同类判定连续失败会逐步提高成功率——每类按自己的保底阈值均分
  `PITY_BONUS_MAX = 3.0` logit（如关系判定 12 次失败累计 +3），**不再达到阈值就 `p = 1.0` 必成**。
  计数在 `state.pity[jtype]`，读档继续累计；成功则 `fails` 归零。
- **不给保底的情况**（`game._handle_judgment`）：`failure_consequence` 为 `injury` / `death` 时
  `allow_pity=False`（渐进保底也不生效）；`certain_failure=True` 直接 `return False, 0.0`
  （不掷骰、不吃保底，天赋与保命能力都救不了）。
- **保命能力** `DEATH_PROTECTIONS = {"古玉护佑": 1}`。条目可显式带 `death_protection_uses`(1~3)。
  致命**战斗伤害**与判定致死（`failure_consequence=death`，或 `injury` 把血扣到 0）都会先消耗
  （P0-3，共用 `game.Game._try_death_protection`）；`certain_failure=true` 的必死行为不触发。

### `combat.py` —— 战斗判定

```
stage = min(玩家战力, 敌人战力) / realm_gap
sigma = realm_gap × k / (1 + stage × decay)      # 段位越高曲线越陡 → 越难跨级
win_logit  = (玩家战力 - 敌人战力) / sigma  + mods.win_bonus
flee_logit = (玩家战力 - 敌人战力 + flee_bonus×realm_gap) / sigma + mods.flee_bonus
```
- `CURVES`：`realm_gap`（默认，k=1.0, decay=0.30, flee_bonus=0.6, base_damage=20, damage_scale=0.10）
  与 `flat`（decay=0, base_damage=15）。
- **`base_damage` 是生命上限的百分比**（P0-2）：`base_damage=20` → 20%×`max_hp`，
  默认 `max_hp=100` 时与旧行为完全一致。`resolve(..., max_hp=)` 由 `game._get_max_hp()` 传入。
- 胜利伤害随优势衰减（`combat.win_damage_multiplier`）：领先 ≥1 个 `realm_gap` 无伤，
  领先 0~1 个 `realm_gap` 从 `40%` 线性降到 0，势均力敌/对手更强保持 `40%`；逃跑成功无伤。
- **胜利永不致死**：`outcome == "win"` 时 `hp_after = max(1, ...)`、`player_down = False`（P0-1）。
- `mods` 由 `game.py` 从天赋/道具算好传入：`power_boost` / `win_bonus` / `flee_bonus` / `flee_guarantee`。
- **概率数值绝不发给模型**，只发 `gm_brief()` 的定性简报（"胜利/落败/负伤程度：重伤"）。
- **非致命切磋**：`combat.lethal = false` → 生命耗尽改为 `hp=1` + `["昏迷","失去战斗能力"]`，不死亡。
  ⚠️ **不能**用来回避真实致命危险（提示词与规则都明确禁止）。

### `dynamics.py` —— 好感度

```
有效好感度 = 基础值 + 相识时长加成（每 5 回合 +1，封顶 +15）+ 关系事件增减，钳制 0~100
```
- `RELATION_EVENTS`：救命之恩/并肩作战/共同经历/赠礼/相谈甚欢/冒犯/误会/欺骗/背弃 等。
- 判定 `relationship` / `persuasion` 时，若 `subject` 是已知角色，`game._handle_judgment` 会把有效好感度
  换算成 `当前好感度` / `对方好感度` 因素（`(eff-50)/50*3` → -3~+3），并**覆盖**模型声明（P1-6）。
- `ATTRIBUTE_EVENTS`：声望/魅力/福缘/体力/修为 等按事件或显式 `delta` 增减。
- **属性参与判定**（P2-19）：`judgment.ATTRIBUTE_FACTOR_LINKS` 把「福缘 → 寻宝的福缘/运气」、
  「魅力 → 说服的口才/魅力」映射成因素，由代码按 `属性值 / ATTRIBUTE_FACTOR_SCALE(10)`
  换算成 -3~+3 并**覆盖**模型填写；缺失/文字属性按 0（普通人），别名写法的同义因素也会被替换。

---

## 6. 改动铁律（Agent 必须遵守）

1. **保持"代码裁决、模型叙述"边界。** 不要让模型自己决定成败/伤害/生死。
   新增判定请走 `judgment`/`combat` 工具字段 + 代码结算。
2. **改完必须跑全量测试**（§1 的命令），基线 132 passed。
   破坏 `tests/test_gameplay.py` 里的边界用例 = 架构退化。
3. **HP / 死亡 / options / 战力 / 好感 / 生命上限是系统独占字段。**
   任何新路径都要像 `_protect_hp` 那样保证代码值最终胜出：模型写不进
   `hp`、`attribute_events[].attribute=="hp"`、`characters[].affinity|met_turn`；
   `player_power` 单次变化 ≤ ±1 个 `realm_gap`，`max_hp` 单轮 ≤ 0.5×~1.5×（P0-2），
   且不超过 `world_state.MAX_HP_CEILING`（绝对安全上限）；hp 只接受有限数字（字符串/NaN/负数被丢弃或归零）。
4. **失败要可回滚。** 新的结算副作用（扣血、扣道具、扣保底）必须发生在 `_play_turn` 的快照与
   `save()` 之间，保证第二次 API 调用失败时能干净撤销。
5. **去数据化。** 玩家可见的文字（叙事 / `to_player_summary()`）**不得出现** hp、战力、好感度、概率、伤害数值。
   内部数值只留在 `state.json`。
6. **增量合并，不要整表覆盖。** 新字段默认"未给出则保留原值"。
   例外只有 `player_status_effects`（整表覆盖）。
7. **背包按数量增减，不整类删除**（`remove_all:true` 除外）。货币就是普通数量型道具。
8. **保持零第三方依赖的核心。** 除网页版外不要引入 pip 包（`requirements.txt` 只有 streamlit）。
   HTTP 用 `urllib`，别换成 `requests`。
9. **测试要隔离存档：** 用 `unittest.mock.patch.object(module, "SAVE_DIR", tmpdir)` 同时 patch
   `memory` 和 `world_state` 两个模块的 `SAVE_DIR`（见 `test_gameplay.setUp`），否则会污染 `save/`。
   测试**不调用真实 API**，用假的 LLM 对象（鸭子类型：`generate` / `summarize` / `call_tool`）。
10. **`config.json` 与 `save/` 永不提交**；不要把 API Key 写进任何受版本控制的文件。
11. **文案与注释保持中文**，与现有代码风格一致（本项目全中文注释/提示词）。
12. **不要为"顺便优化"重构前端。** 三个前端有历史分歧（§8），改一处前先确认另外两处是否需要同步。

---

## 7. 常见改动地图（"我要改 X，该动哪里"）

| 想改什么 | 改哪里 |
|---------|--------|
| 判定类型/权重/基础概率 | `judgment.JUDGMENT_TYPES` |
| 天赋/体质/金手指的数值系数 | `judgment.TALENT_MODIFIERS`（+ 等级倍率 `TIER_SCALE`） |
| 自定义项关键词推断 | `judgment.CUSTOM_MODIFIER_KEYWORDS` / `_MAX_CAT` |
| 道具效果 | `judgment.ITEM_MODIFIERS`（`consumable` 控制是否消耗） |
| 保命能力可抵挡次数 | `judgment.DEATH_PROTECTIONS`，或状态条目里的 `death_protection_uses` |
| 战斗曲线/伤害 | `combat.CURVES` |
| 好感度公式/关系事件 | `dynamics.py` 的 `effective_affinity` / `RELATION_EVENTS` / `ATTRIBUTE_EVENTS` |
| 主持人规则（自主权、公平性…） | `gm_rules.json` 的 `rules` 数组（或 `gm_rules.DEFAULT_RULES` 改默认值）。**两处要同步**，否则删掉 json 后旧规则会从默认值复活 |
| 内容尺度 | **本作面向成年玩家，不设分级/尺度限制**。正面说明在 `prompts.SYSTEM_PROMPT` 的【内容尺度——成人向】，其「底线」一行要求：涉及性的角色一律是成年人。如需额外收紧，在 `gm_rules.json` 的 `rules` 里加 |
| 叙事质量（NPC 主动性、爽感、每轮停在决策点） | `prompts.SYSTEM_PROMPT` 的【叙事质量】 |
| 岁月模式：时长档位、打断/插曲概率、回血率、事件表 | `seasons.py`：`DURATIONS` / `INTERRUPT_RATE` / `FLAVOR_RATE` / `HEAL_PER_SLICE` / `FLAVOR_EVENTS` / `INTERRUPT_EVENTS` |
| 岁月模式的结算与打断 | `game.Game._resolve_block`（日历/年龄/回血/`pending_block`）、`game.Game.run_block` / `resume_block`；**结算语义不要改** |
| 幕后进展保留条数 | `world_state.MAX_OFFSCREEN`（默认 10） |
| 能力代价开关 | `character_builder.ENABLE_DRAWBACKS`（v2.19 默认 False；提示词里的代价段落已删除） |
| 天赋加成上限 | `judgment.TALENT_BONUS_CAP`（默认 4.0 logit） |
| NPC 往事 / 初始好感 | `world_state._add_memory` / `MAX_CHARACTER_MEMORIES`；`dynamics.initial_affinity` / `INITIAL_AFFINITY` |
| 一键天命的品级权重 | `app._AUTO_TIER_WEIGHT` |
| 系统提示的行为约束 | `prompts.SYSTEM_PROMPT` 正文 + `build_system_prompt()` 的拼接顺序 |
| 模型能写哪些状态字段 | `tools.UPDATE_TOOL` 的 JSON Schema **（改 schema 必须同步 `world_state.apply_update`）** |
| 存档历史/审计（SQLite） | `storage.py`；`WANJIE_STORAGE=sqlite` 开启双写；迁移用 `scripts/migrate_json_to_sqlite.py` |
| 上下文长度/压缩频率 | `config.py`：`KEEP_RECENT_ENTRIES=6` / `COMPRESS_EVERY_TURNS=20` / `MAX_BUFFER_ENTRIES=80` / `COMPRESS_MAX_FAILURES=3` / `COMPRESS_RETRY_TURNS=5` / `MAX_EVENT_LOG=15` / `MAX_CHAPTER_SUMMARIES=6` |
| 模型名/接口地址 | `config.DEFAULT_MODEL` / `config.BASE_URL`（或 `config.json` 的 `model`） |
| 天赋候选池的生成/去重/保底 | `character_builder.py`（`POOL_TARGET_COUNTS` / `_ensure_floor` / `sample_hand`） |
| 世界观生成与归一化 | `world_gen.py`（未知字段进 `custom` 保留） |
| 网页界面 | `app.py`（注意 ~1100 行的单文件结构） |
| tkinter 界面 | `gui.py` |
| CLI 菜单 | `main.py` |

### 扩展时的成对改动清单

- **新增判定类型**：`judgment.JUDGMENT_TYPES` + `tools.UPDATE_TOOL.judgment.type.enum`（两处必须同步）。
- **新增状态字段**：`world_state.empty_state()` + `apply_update()` + `tools.UPDATE_TOOL` + `to_summary()`（要让模型看到）。
- **新增天赋/体质/金手指**：`character_builder` 的目录/池 + `judgment.TALENT_MODIFIERS`（否则无系数，只靠关键词推断）。
- **新增保命能力**：`judgment.DEATH_PROTECTIONS` + `prompts.build_system_prompt()` 的保命段落。

---

## 8. 已知分歧与技术债（**动这些地方前务必确认**）

1. **三个前端的功能不对等 —— 最大的坑。**
   - `app.py`（网页）是**功能最全**的界面：使用 **v2 候选池**
     （`character_builder.generate_pool` / `sample_hand` / `prepare_pool` / `POOL_TARGET_COUNTS` = 24/18/18，
     三次并行 API 调用、按名去重、至少补足 18 项、`_ensure_floor` 保底高等级项、锁定/换组/重新生成）。
   - `gui.py` 与 `main.py` **仍使用 legacy 固定目录**（`TALENTS` / `PHYSIQUES` / `GOLDEN_FINGERS` +
     `roll_talents` / `roll_physique` / `roll_build`），**没有**候选池、等级、代价（`drawback`）体系。
   - 结论：**在网页版看到的"候选池/等级颜色/代价"行为，GUI 和 CLI 里不存在。**
     若要统一，工作量在 `gui.CharacterDialog` 与 `main.build_character`。
   - **后果很隐蔽（`build_player` 的调用参数不同）**：
     `character_builder.build_player(template, talents, physique, gf, descriptions=, tiers=, drawbacks=, player_name=)`
     - `app.render_character_build()` **传了** `descriptions` / `tiers` / `drawbacks` → 天赋带 `tier` 与 `drawback`。
     - `main.build_character()` 与 `gui.CharacterDialog._on_confirm()` **都没传** → 条目缺 `tier` / `drawback`。
     由于 `judgment.talent_bonus()` 用 `TIER_SCALE.get(tier, 1.0)`（缺 tier 就是 **1.0**），
     **等级倍率（如炫彩 ×5.0）只有从网页版开局才会真正生效。**（v2.19 起代价机制已整体关闭。）
   - **金手指数量不一致**：数据结构支持 1~3 个（`golden_fingers` 列表），
     `app.render_character_build()` 会传列表（`gf_list[:3]`），但 `main.build_character()`（单个名字）与 `gui.CharacterDialog`（单选框）
     **只能给出 1 个**。
2. **重复实现的小工具**（改一个别忘了另一个）：
   - `app.affinity_label()` 与 `gui._affinity_label()` —— 好感度定性标签，两份独立实现；
     同样的 70/40/15 阈值在 `world_state.to_player_summary()` 里还有**第三份**内联副本。
   - `gui._hp_band()` 与 `world_state.WorldState._hp_band()` —— 血量定性分档，两份独立实现。
   - 手动输入世界观的模板 dict 字面量在 `main._manual_template()` 区段、`gui.SetupDialog._manual_template()`、`app` 的手动模板分支各写了一遍。
   - `app.py` 还自行实现了一部分判定/存档编排逻辑，而非全部复用 `game.Game`。
3. **存档/读档有三套实现**：`main.continue_game()` → `game.load_game(llm)`；
   `gui.GameApp._continue()` **绕过 `load_game()`**，自己 `Game(WorldState.load(), Memory.load(), llm)`；
   `app.py` 用 `session_slot()` / `_save_to_named_slot()` / `_load_named_slot()` + `save_bundle` 导入导出。
   **多存档槽（`save_key`）只有网页版有**，CLI 与 tkinter 版只能读写默认槽。
4. **`README.md` 是按版本号（v2.15 / v2.16 / v2.17 …）堆叠的功能清单**，没有统一的架构视图，
   且与代码存在局部滞后。**改行为后建议同步更新 README 的对应章节。**
5. **`万界.txt` 与 `九天万界.docx` 是设计参考稿**（"九天万界·超高自由度真实修仙人生模拟器"协议文档），
   **不被任何代码引用**，不是运行时依赖。不要把它们接进代码路径。
6. **`gui.py` 是 PyInstaller 的打包入口**（`game.spec` 的 `Analysis(['gui.py'])`），
   打包依赖 `cacert.pem`（`datas`）与 `icon.icns`。
   打包只能在**目标系统**上做：macOS 出 `.app`（`./build_macos.sh`），Windows 出 `.exe`
   （`pyinstaller game.spec`，需在 Windows 机器上跑）。
   macOS 采用**单文件自包含**打包，避免 iCloud 同步破坏 bundle 内软链。
7. **无流式输出**：`llm_client.chat()` 固定 `stream: False`，`timeout=180`，`temperature=0.8`（摘要 0.4）。
   战斗/判定轮要**两次** API 调用，所以更慢。
8. **API 层无重试**：`llm_client._post()` 固定 180s 超时。v2.18 起超时、连接中断、返回内容无法解析、
   缺 `choices`/`message` 都统一转成 `LLMError`。`parse_reply()` 遇到工具参数 JSON 损坏时
   返回 `update=None`，会走 §3 的"重试一次"路径而不是报错。历史压缩失败（任何异常）都不会回滚本轮。
9. **`update_world_state` 的根级 `parameters` 没有 `required`**（`tools.py:18`）：
   模型合法地可以返回 `{}`。任何消费方都必须容忍空增量（`apply_update` 已逐字段守卫）。
10. **存档写入**（v2.18）：`config.write_json_atomic()` 先写同目录的 `.xxx.tmp` 再 `os.replace`。
    `WorldState.load()` 遇到损坏文件抛 `config.SaveCorruptError`（三个前端都已提示）；
    `Memory.load()` 遇到损坏文件返回空记录（世界状态才是一致性来源）。
11. **容错**：`apply_update` 对每个字段做类型守卫，格式不对的单项跳过而不是让整轮报错；
    文字型属性（如 `修为="筑基初期"`）不参与数值增减。`world_gen.normalize_template` 接受
    `inventory` 为字符串列表 / `{名: 数量}`，`characters` 为列表 / `{名: 描述}`。
12. **SQLite 存储（阶段 1 第一步）**：`storage.py` 是可选审计层，JSON 仍是主存储；
    开启双写用环境变量 `WANJIE_STORAGE=sqlite`。**不要把 .db 当主存储读**（读档仍走 JSON）。
13. **CI（待启用）**：工作流模板在 `docs/ci-tests.yml`（`py_compile` + 全量测试，不调用 API），
    `.github/workflows/tests.yml` 也已放好，但当前 PAT 缺 `workflow` scope 推不上去；
    补权限后 `git add .github/workflows/tests.yml && git commit && git push` 即可启用。

---

## 9. 给 Agent 的工作流建议

1. **先跑基线测试**（§1），确认 132 passed，再动手。
2. **定位改动点**：查 §7 的地图，别一上来就通读 6000 行。
   需要细节时按职责读单个文件，优先读 `game.py` + `world_state.py` + 目标文件。
3. **改代码 → 跑测试 → 破坏边界用例就回退。**
4. **需要新回归用例时**：加进 `tests/test_gameplay.py`（引擎行为）或对应 UI 测试文件。
   区分引擎测试（纯 mock LLM）与 UI 测试（`streamlit.testing.v1.AppTest`，缺 streamlit 时 `AppTest=None` 自动跳过）。
5. **改完更新**：`README.md` 对应章节（用户可见行为）+ 本文件（若是架构/铁律变化）。
6. **不要**：提交 `config.json`/`save/`、引入 pip 依赖到核心、把 API Key 写进文件、
   让模型获得裁决权、在玩家可见文本里暴露数值。

---

## 10. 快速参考卡片

```bash
# 全量测试（唯一可靠命令，132 passed）
.venv/bin/python -m unittest tests.test_gameplay tests.test_character_pool tests.test_character_pool_ui tests.test_chat_ui

# 启动
.venv/bin/python -m streamlit run app.py   # 网页版（功能最全）
.venv/bin/python main.py                   # CLI
.venv/bin/python gui.py                    # tkinter

# 单文件语法检查
.venv/bin/python -m py_compile app.py game.py world_state.py
```

| 关键常量 | 值 | 位置 |
|---------|----|------|
| `KEEP_RECENT_ENTRIES` | 6（= 最近 3 轮） | `config.py` |
| `COMPRESS_EVERY_TURNS` | 20 | `config.py` |
| `MAX_EVENT_LOG` | 15 | `config.py` |
| `MAX_CHAPTER_SUMMARIES` | 6 | `config.py` |
| `DEFAULT_MODEL` | `deepseek-chat` | `config.py` |
| `BASE_URL` | `https://api.deepseek.com` | `config.py` |
| `POOL_TARGET_COUNTS` | talents 24 / physiques 18 / golden_fingers 18 | `character_builder.py` |
| `MAX_TALENTS` / `HAND_SIZE` / `MIN_POOL_SIZE` | 3 / 9 / 18 | `character_builder.py` |
| `TIER_SCALE["炫彩"]` | 5.0 | `judgment.py` |
| `_MAX_CAT` | 1.5 | `judgment.py` |
| `DEATH_PROTECTIONS` | `{"古玉护佑": 1}` | `judgment.py` |
| `SLICE_DAYS` / `MAX_BLOCK_DAYS` | 10（一旬）/ 3600 | `seasons.py` |
| `DURATIONS` | 一旬10 / 一月30 / 一季90 / 半年180 / 一年360 | `seasons.py` |
| `INTERRUPT_RATE` / `FLAVOR_RATE` | 普通 0.08/0.25，闭关 0.03/0.10（每旬） | `seasons.py` |
| `MAX_OFFSCREEN` | 10 | `world_state.py` |
| `ATTRIBUTE_FACTOR_SCALE` | 10（每 10 点属性 ≈ ±1 因素） | `judgment.py` |
| `MAX_BUFFER_ENTRIES` / `COMPRESS_MAX_FAILURES` / `COMPRESS_RETRY_TURNS` | 80 / 3 / 5 | `config.py` |
| `SNAPSHOT_EVERY_TURNS` | 10（SQLite 快照间隔） | `storage.py` |

**游戏内命令**：`/state` 查看状态 · `/rules` 查看主持人规则 · `/save` 保存 · `/quit` 保存退出 · `/help` 帮助
