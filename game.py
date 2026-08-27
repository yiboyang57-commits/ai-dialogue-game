"""游戏编排：每轮流程、战斗/非战斗判定、状态更新、历史压缩、存读档。"""
import combat as combat_module
import dynamics as dynamics_module
import judgment as judgment_module
from config import MAX_CHAPTER_SUMMARIES
from gm_rules import load_rules
from memory import Memory
from prompts import build_system_prompt, build_turn_messages, build_compress_messages, parse_compress
from tools import apply_update
from world_state import WorldState


OPENING_INSTRUCTION = (
    "请开始游戏：根据初始情境写出开场叙事，并调用 update_world_state 初始化世界状态"
    "（当前地点、时间、初始角色、背包、剧情标记、当前目标等）。"
)


def merge_updates(a, b):
    """合并两次工具调用的状态增量（判定回合会有两次调用）。"""
    a = a or {}
    b = b or {}
    if not a:
        return dict(b)
    if not b:
        return dict(a)
    out = dict(a)
    for k, v in b.items():
        if k == "characters":
            merged = dict(out.get(k) or {})
            for name, info in (v or {}).items():
                if name in merged and isinstance(merged[name], dict) and isinstance(info, dict):
                    merged[name] = dict(merged[name])
                    merged[name].update(info)
                else:
                    merged[name] = info
            out[k] = merged
        elif k in ("plot_flags", "player_attributes"):
            merged = dict(out.get(k) or {})
            merged.update(v or {})
            out[k] = merged
        elif k in ("inventory_add", "inventory_remove", "relationship_events", "attribute_events"):
            out[k] = list(out.get(k) or []) + list(v or [])
        elif k == "options":
            if not out.get("options"):
                out[k] = v  # 可选行动以第一段（主回合）为准
        elif k == "turn_event":
            if v:
                out[k] = (out[k] + "；" + v) if out.get("turn_event") else v
        else:
            out[k] = v
    return out


class Game:
    def __init__(self, state, memory, llm, rules=None):
        self.state = state
        self.memory = memory
        self.llm = llm
        self.rules = rules if rules is not None else load_rules()
        self.system_prompt = build_system_prompt(state, self.rules)

    def _refresh_system_prompt(self):
        # 战力/天赋等锚定可能变化，每轮重建，保证发给模型的是最新值
        self.system_prompt = build_system_prompt(self.state, self.rules)

    # ---- 每轮核心流程 ----
    def _generate(self, player_input):
        """只把 [状态摘要 + 最近几轮 + 最新输入] 发给模型，拿到 (叙事, 状态增量)。"""
        self._refresh_system_prompt()
        messages = [{"role": "system", "content": self.system_prompt}]
        messages += build_turn_messages(self.state, self.memory, player_input)
        text, update = self.llm.generate(messages)

        if not update:
            retry = messages + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": "你还没有调用 update_world_state 工具。请现在调用它，记录本轮的世界状态变化。",
                },
            ]
            text2, update = self.llm.generate(retry)
            if not update:
                update = {"turn_event": "本轮未提供结构化更新"}
            else:
                text = text2 or text
        return text, update

    def _commit(self, player_input, text, update, add_user):
        apply_update(self.state, update)
        self.state.bump_turn()
        if add_user:
            self.memory.add_user(player_input)
        self.memory.add_assistant(text)
        if self.memory.needs_compression():
            self.compress()
        self.save()

    # ---- 判定（战斗 + 非战斗）统一入口 ----
    def _handle_rolls(self, text, update):
        if not isinstance(update, dict):
            return text, update
        if update.get("combat"):
            return self._handle_combat(text, update)
        if update.get("judgment"):
            return self._handle_judgment(text, update)
        return text, update

    # ---- 天赋 / 体质 / 金手指 / 道具辅助 ----
    def _talent_names(self):
        """返回会影响判定的“能力名”列表：自选天赋 + 体质 + 金手指。"""
        p = self.state.data.get("player", {})
        talents = p.get("talents") or []
        out = []
        for t in talents:
            out.append(t.get("name") if isinstance(t, dict) else str(t))
        physique = p.get("physique") or {}
        if physique.get("name"):
            out.append(physique["name"])
        golden_finger = p.get("golden_finger") or {}
        if golden_finger.get("name"):
            out.append(golden_finger["name"])
        return [x for x in out if x]

    def _consume_item(self, name):
        inv = self.state.data.get("inventory") or []
        for entry in inv:
            if entry.get("name") == name:
                entry["quantity"] = int(entry.get("quantity", 1)) - 1
        self.state.data["inventory"] = [i for i in inv if int(i.get("quantity", 0)) > 0]

    # ---- 战斗判定 ----
    def _get_hp(self):
        attrs = self.state.data.get("player", {}).get("attributes") or {}
        hp = attrs.get("hp")
        return int(hp) if isinstance(hp, (int, float)) else 100

    def _handle_combat(self, text, update):
        req = update.get("combat")
        anchor = self.state.data.get("combat") or {}
        player_power = anchor.get("player_power") or 0
        realm_gap = anchor.get("realm_gap") or 5
        curve = anchor.get("curve") or "realm_gap"
        params = combat_module.curve_params(curve)

        enemy_power = req.get("enemy_power")
        if not isinstance(enemy_power, (int, float)):
            enemy_power = player_power
        action = req.get("action") or "fight"
        talents = self._talent_names()

        mods = {
            "win_bonus": judgment_module.talent_bonus("combat", talents),
            "flee_bonus": judgment_module.talent_bonus("flee", talents),
        }
        use_item = req.get("use_item")
        if use_item:
            im = judgment_module.item_mods(use_item)
            mods["power_boost"] = im.get("combat_power") or 0
            mods["win_bonus"] += im.get("combat") or 0
            mods["flee_bonus"] += im.get("flee") or 0
            if im.get("flee_guarantee"):
                mods["flee_guarantee"] = True
            if im.get("consumable"):
                self._consume_item(use_item)

        hp = self._get_hp()
        result = combat_module.resolve(player_power, enemy_power, realm_gap, action, params, hp, mods)
        self._apply_combat_result(req, result)

        brief = combat_module.gm_brief(result, hp)
        outcome_text, outcome_update = self._generate_outcome(brief)

        text = (text + "\n\n" + outcome_text) if text else outcome_text
        merged = merge_updates(update, outcome_update)
        merged.pop("combat", None)
        if isinstance(merged.get("player_attributes"), dict):
            merged["player_attributes"] = {k: v for k, v in merged["player_attributes"].items() if k != "hp"}
        return text, merged

    def _apply_combat_result(self, req, result):
        d = self.state.data
        attrs = d.setdefault("player", {}).setdefault("attributes", {})
        attrs["hp"] = result["player_hp_after"]
        d["last_combat"] = {
            "enemy_name": req.get("enemy_name"),
            "enemy_power": req.get("enemy_power"),
            "outcome": result["outcome"],
            "enemy_defeated": result["enemy_defeated"],
            "player_hp_after": result["player_hp_after"],
        }

    # ---- 非战斗判定 ----
    def _handle_judgment(self, text, update):
        req = update.get("judgment")
        jtype = req.get("type") or "action"
        subject = req.get("subject") or ""
        factors = dict(req.get("factors") or {})
        talents = self._talent_names()

        # 对象是已知角色时，自动把“有效好感度(含相识时长)”换算为“当前好感度”因素（0~100 → -3~+3）
        chars = self.state.data.get("characters") or {}
        if subject in chars and isinstance(chars[subject].get("affinity"), (int, float)):
            turn = self.state.data.get("meta", {}).get("turn", 0)
            eff = dynamics_module.effective_affinity(chars[subject], turn)
            factors.setdefault("当前好感度", round((eff - 50) / 50 * 3))

        item_bonus = 0.0
        use_item = req.get("use_item")
        if use_item:
            im = judgment_module.item_mods(use_item)
            item_bonus += im.get(jtype, 0.0)
            if im.get("consumable"):
                self._consume_item(use_item)
        # 常驻道具被动加成（如“寻宝罗盘”）
        for inv in self.state.data.get("inventory") or []:
            im = judgment_module.item_mods(inv.get("name"))
            if not im.get("consumable") and im.get(jtype):
                item_bonus += im[jtype]

        pity = self.state.data.setdefault("pity", {}).setdefault(jtype, {"fails": 0, "successes": 0})
        success, _p = judgment_module.resolve_judgment(jtype, factors, talents, item_bonus, pity)
        brief = judgment_module.judgment_brief(jtype, success, subject)

        outcome_text, outcome_update = self._generate_outcome(brief)
        text = (text + "\n\n" + outcome_text) if text else outcome_text
        merged = merge_updates(update, outcome_update)
        merged.pop("judgment", None)
        return text, merged

    def _generate_outcome(self, brief):
        """根据系统判定结果，让模型叙述结果并记录后续状态。"""
        self._refresh_system_prompt()
        summary = self.state.to_summary()
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": (
                "【系统判定结果（由系统决定，请严格遵守，不得改写结局）】\n" + brief + "\n\n"
                "请用与世界观一致的风格，简洁地续写这个结果（2~5 句话），"
                "并调用 update_world_state 记录后续状态变化（战利品、剧情标记、目标、好感度、玩家战力提升、状态效果等）。"
                "注意：不要设置或修改 hp（生命变化已由系统结算）；不要输出 options（可选行动由主回合给出）。\n\n"
                "【当前状态摘要】\n" + summary
            )},
        ]
        text, update = self.llm.generate(messages)
        return text or "", update

    # ---- 公开入口 ----
    def start(self):
        """开场：根据初始情境生成开场叙事并初始化状态。"""
        text, update = self._generate(OPENING_INSTRUCTION)
        text, update = self._handle_rolls(text, update)
        self._commit(None, text, update, add_user=False)
        return text

    def run_turn(self, player_input):
        text, update = self._generate(player_input)
        text, update = self._handle_rolls(text, update)
        self._commit(player_input, text, update, add_user=True)
        return text

    # ---- 历史压缩 ----
    def compress(self):
        buffer_text = self.memory.format_buffer()
        if buffer_text.strip():
            msgs = build_compress_messages(self.state.data.get("narrative_summary"), buffer_text)
            raw = self.llm.summarize(msgs)
            chapter, global_summary = parse_compress(raw, self.state.data.get("narrative_summary"))
            if global_summary:
                self.state.data["narrative_summary"] = global_summary
            if chapter:
                chapters = self.state.data.setdefault("chapter_summaries", [])
                chapters.append(chapter)
                if len(chapters) > MAX_CHAPTER_SUMMARIES:
                    del chapters[: len(chapters) - MAX_CHAPTER_SUMMARIES]
        self.memory.reset_after_compression()

    # ---- 持久化 ----
    def save(self):
        self.state.save()
        self.memory.save()


def load_game(llm, save_key=""):
    """读档：若存在存档则返回 Game，否则返回 None。save_key 用于多存档隔离。"""
    state = WorldState.load(save_key)
    if state is None:
        return None
    memory = Memory.load(save_key)
    return Game(state, memory, llm)
