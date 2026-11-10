"""游戏编排：每轮流程、战斗/非战斗判定、状态更新、历史压缩、存读档。"""
import copy
import json
import combat as combat_module
import dynamics as dynamics_module
import judgment as judgment_module
import seasons
from config import MAX_CHAPTER_SUMMARIES
from gm_rules import load_rules
from llm_client import LLMError
from memory import Memory
from prompts import build_system_prompt, build_turn_messages, build_compress_messages, parse_compress
from tools import apply_update
from world_state import WorldState, safe_int


OPENING_INSTRUCTION = (
    "请开始游戏：根据初始情境写出开场叙事，并调用 update_world_state 初始化世界状态"
    "（当前地点、时间、初始角色、背包、剧情标记、当前目标等）。"
    "主角的姓名与设定已在开局前确定（见状态摘要），直接使用即可；"
    "不要再次询问玩家姓名、性别、年龄、灵根或让其重新设定角色，直接推进剧情。"
    "只呈现当前场景，不替玩家行动、不跳时间。模板中的背包已经发放，不能用 inventory_add 重复发放。"
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
            out[k] = v  # 必须以最终结果之后的行动选项为准
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
        return self._call_world_update(messages)

    def _call_world_update(self, messages):
        text, update = self.llm.generate(messages)
        if not isinstance(update, dict):
            retry = messages + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": "请只补上 update_world_state 工具调用，记录这次行动的状态增量，不再推进剧情或重复发放物品。",
                },
            ]
            text2, update = self.llm.generate(retry)
            if not isinstance(update, dict):
                raise LLMError("本轮未获得有效状态更新，请重试；本轮行动尚未结算。")
            text = text or text2
        return text, update

    def _commit(self, player_input, text, update, add_user):
        already_dead = self.state.is_dead
        cause = (self.state.data.get("player") or {}).get("death_cause")
        apply_update(self.state, update)
        if already_dead or self.state.is_dead:
            self.state.mark_dead(cause or update.get("turn_event") or "生命耗尽")
        self.state.bump_turn()
        if add_user:
            self.memory.add_user(player_input)
        self.memory.add_assistant(text)
        if self.memory.needs_compression():
            try:
                self.compress()
            except Exception:
                # 压缩失败（含超时、断线等任何异常）不抹掉本轮，也不清空待压缩内容，下轮再尝试。
                pass
        self.save()

    # ---- 判定（战斗 + 非战斗）统一入口 ----
    @staticmethod
    def _has_roll(update):
        return isinstance(update, dict) and any(isinstance(update.get(k), dict) and bool(update[k])
                                                for k in ("combat", "judgment", "time_block"))

    def _handle_rolls(self, text, update, player_input):
        if not isinstance(update, dict):
            return text, update
        # 格式不对的判定声明（不是对象）当作没有判定，避免整轮报错。
        for key in ("combat", "judgment", "time_block"):
            if key in update and not isinstance(update[key], dict):
                update = dict(update)
                update.pop(key)
        if update.get("combat"):
            return self._handle_combat(text, update, player_input)
        if update.get("judgment"):
            return self._handle_judgment(text, update, player_input)
        if update.get("time_block"):
            context_update = self._prepare_roll(update)
            return self._resolve_block(update["time_block"], player_input, draft=text, context_update=context_update)
        return text, update

    def _prepare_roll(self, update):
        """只接受判定前的场景变化，丢弃模型提前猜测的奖励、胜负和行动选项。"""
        context = {k: update[k] for k in ("location", "location_description", "time") if k in update}
        chars = {}
        for name, info in (update.get("characters") or {}).items():
            if isinstance(info, dict):
                before_result = {k: info[k] for k in ("description", "attitude_toward_player", "location") if k in info}
                if before_result:
                    chars[name] = before_result
        if chars:
            context["characters"] = chars
        apply_update(self.state, context)
        return context

    @staticmethod
    def _protect_hp(update, hp):
        update = dict(update)
        attrs = update.get("player_attributes")
        attrs = dict(attrs) if isinstance(attrs, dict) else {}
        attrs["hp"] = hp
        update["player_attributes"] = attrs
        update["attribute_events"] = [ev for ev in (update.get("attribute_events") or [])
                                      if isinstance(ev, dict) and ev.get("attribute") != "hp"]
        return update

    def _guard_vitals(self, update, rolled):
        """生命值是系统字段：普通回合模型不能扣血或致死，只能回血且不超过上限。

        受伤与死亡必须通过 combat / judgment(injury|death) 由代码结算。
        max_hp 只接受 >=1 的数字（突破等可以提高上限，但本轮回血仍按原上限封顶）。
        """
        if not isinstance(update, dict):
            return update
        update = dict(update)
        attrs = update.get("player_attributes")
        attrs = dict(attrs) if isinstance(attrs, dict) else {}
        cur = (self.state.data.get("player") or {}).get("attributes") or {}

        if "max_hp" in attrs:
            new_max = attrs["max_hp"]
            if not (isinstance(new_max, (int, float)) and not isinstance(new_max, bool) and new_max >= 1):
                attrs.pop("max_hp")
        if not rolled:
            # 回血上限按本轮之前的上限算：同一轮里先抬高上限再回满血不生效。
            max_hp = cur.get("max_hp")
            max_hp = max_hp if isinstance(max_hp, (int, float)) and max_hp >= 1 else 100
            hp_now = self._get_hp()
            heal_to = None
            want = attrs.pop("hp", None)
            if isinstance(want, (int, float)) and not isinstance(want, bool) and want > hp_now:
                heal_to = want
            events = []
            for ev in update.get("attribute_events") or []:
                if isinstance(ev, dict) and ev.get("attribute") == "hp":
                    try:
                        delta = int(ev.get("delta"))
                    except (TypeError, ValueError):
                        continue
                    if delta > 0:
                        heal_to = max(heal_to or hp_now, hp_now + delta)
                    continue
                events.append(ev)
            if "attribute_events" in update:
                update["attribute_events"] = events
            if heal_to is not None and not self.state.is_dead:
                attrs["hp"] = int(min(heal_to, max_hp))
        if attrs or "player_attributes" in update:
            update["player_attributes"] = attrs
        return update

    # ---- 天赋 / 体质 / 金手指 / 道具辅助 ----
    def _talent_entries(self):
        """返回 [(name, tier)]：自选天赋 + 体质 + 金手指（tier 影响判定强度）。"""
        p = self.state.data.get("player", {})
        out = []
        for t in (p.get("talents") or []):
            if isinstance(t, dict):
                out.append((t.get("name"), t.get("tier")))
            else:
                out.append((str(t), None))
        physique = p.get("physique") or {}
        if physique.get("name"):
            out.append((physique.get("name"), physique.get("tier")))
        gfs = p.get("golden_fingers")
        if isinstance(gfs, list):
            for g in gfs:
                if isinstance(g, dict) and g.get("name"):
                    out.append((g.get("name"), g.get("tier")))
        else:
            golden_finger = p.get("golden_finger") or {}
            if golden_finger.get("name"):
                out.append((golden_finger.get("name"), golden_finger.get("tier")))
        return [(n, ti) for n, ti in out if n]

    def _consume_item(self, name):
        inv = self.state.data.get("inventory") or []
        for entry in inv:
            if isinstance(entry, dict) and entry.get("name") == name and safe_int(entry.get("quantity"), 1) > 0:
                entry["quantity"] = safe_int(entry.get("quantity"), 1) - 1
                break
        self.state.data["inventory"] = [i for i in inv if isinstance(i, dict) and safe_int(i.get("quantity"), 1) > 0]

    def _owns_item(self, name):
        return any(isinstance(i, dict) and i.get("name") == name and safe_int(i.get("quantity"), 1) > 0
                   for i in self.state.data.get("inventory") or [])

    def _consume_death_protection(self):
        player = self.state.data["player"]
        used = player.setdefault("death_protections_used", {})
        for name, limit in judgment_module.death_protection_limits(player).items():
            if used.get(name, 0) < limit:
                used[name] = used.get(name, 0) + 1
                return name
        return None

    # ---- 战斗判定 ----
    def _get_hp(self):
        attrs = self.state.data.get("player", {}).get("attributes") or {}
        hp = attrs.get("hp")
        return int(hp) if isinstance(hp, (int, float)) else 100

    def _handle_combat(self, text, update, player_input):
        req = update.get("combat")
        context_update = self._prepare_roll(update)
        anchor = self.state.data.get("combat") or {}
        player_power = anchor.get("player_power") or 0
        realm_gap = anchor.get("realm_gap") or 5
        curve = anchor.get("curve") or "realm_gap"
        params = combat_module.curve_params(curve)

        enemy_power = req.get("enemy_power")
        if not isinstance(enemy_power, (int, float)):
            enemy_power = player_power
        action = req.get("action") or "fight"
        talents = self._talent_entries()

        mods = {
            "win_bonus": judgment_module.talent_bonus("combat", talents),
            "flee_bonus": judgment_module.talent_bonus("flee", talents),
        }
        use_item = req.get("use_item")
        owns_item = bool(use_item and self._owns_item(use_item))
        if owns_item:
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
        nonlethal_down = result["player_down"] and req.get("lethal") is False
        protection = None
        if nonlethal_down:
            result["player_hp_after"] = 1
            result["player_down"] = False
            self.state.data["player"]["status_effects"] = ["昏迷", "失去战斗能力"]
        elif result["player_down"]:
            protection = self._consume_death_protection()
            if protection:
                result["player_hp_after"] = 1
                result["player_down"] = False
                self.state.data["player"]["status_effects"] = ["重伤", protection + "已消耗"]
        self._apply_combat_result(req, result)
        if result["player_down"]:
            self.state.mark_dead("与" + (req.get("enemy_name") or "敌人") + "的冲突中生命耗尽")

        brief = combat_module.gm_brief(result, hp)
        if nonlethal_down:
            brief += " 这是事先明确的非致命冲突，玩家昏迷、失去战斗能力。停在此处，不擅自跳到苏醒或后续获救。"
        if protection:
            brief += " 已有保命能力【" + protection + "】抵挡本次致命一击；次数已消耗并记录，玩家重伤、只保住性命，不能借此自动疗伤、反杀或推进后续剧情。"
        if use_item and not owns_item:
            brief += " 玩家没有该道具，不能虚构其效果。"
        outcome_text, outcome_update = self._generate_outcome(brief, player_input, text, req)

        merged = merge_updates(context_update, outcome_update)
        if nonlethal_down:
            merged["player_status_effects"] = ["昏迷", "失去战斗能力"]
        elif protection:
            merged["player_status_effects"] = ["重伤", protection + "已消耗"]
        return outcome_text, self._protect_hp(merged, result["player_hp_after"])

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
    def _handle_judgment(self, text, update, player_input):
        req = update.get("judgment")
        context_update = self._prepare_roll(update)
        jtype = req.get("type") or "action"
        subject = req.get("subject") or ""
        factors = req.get("factors")
        factors = dict(factors) if isinstance(factors, dict) else {}
        talents = self._talent_entries()

        # 对象是已知角色时，自动把“有效好感度(含相识时长)”换算为“当前好感度”因素（0~100 → -3~+3）
        chars = self.state.data.get("characters") or {}
        if subject in chars and isinstance(chars[subject].get("affinity"), (int, float)):
            turn = self.state.data.get("meta", {}).get("turn", 0)
            eff = dynamics_module.effective_affinity(chars[subject], turn)
            factors.setdefault("当前好感度", round((eff - 50) / 50 * 3))

        item_bonus = 0.0
        use_item = req.get("use_item")
        owns_item = bool(use_item and self._owns_item(use_item))
        if owns_item:
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
        consequence = req.get("failure_consequence") or "setback"
        success, _p = judgment_module.resolve_judgment(
            jtype, factors, talents, item_bonus, pity,
            allow_pity=consequence not in ("injury", "death"),
            certain_failure=req.get("certain_failure") is True,
        )
        brief = judgment_module.judgment_brief(jtype, success, subject)
        hp = self._get_hp()
        if not success and consequence == "death":
            self.state.mark_dead(req.get("reason") or subject or "致命行动失败")
            hp = 0
        elif not success and consequence == "injury":
            damage = req.get("failure_damage")
            damage = int(damage) if isinstance(damage, (int, float)) else 25
            max_hp = self.state.data["player"]["attributes"].get("max_hp") or 100
            hp = max(0, hp - max(1, min(damage, max_hp)))
            self.state.data["player"]["attributes"]["hp"] = hp
            brief += " 玩家受到伤害。"
            if hp <= 0:
                self.state.mark_dead(req.get("reason") or subject or "危险行动中生命耗尽")
        if self.state.is_dead:
            brief += " 玩家已死亡，本次人生已结束；不得改成昏迷、获救、觉醒或复活，options必须为空。"
        if req.get("certain_failure") is True:
            brief += " 此行动依照已有条件确定无法安全完成，没有随机翻盘或保底。"
        if use_item and not owns_item:
            brief += " 玩家没有该道具，不能虚构其效果。"

        outcome_text, outcome_update = self._generate_outcome(brief, player_input, text, req)
        merged = merge_updates(context_update, outcome_update)
        return outcome_text, self._protect_hp(merged, hp)

    def _generate_outcome(self, brief, player_input, draft, request):
        """根据系统判定结果，让模型叙述结果并记录后续状态。"""
        self._refresh_system_prompt()
        messages = [{"role": "system", "content": self.system_prompt}]
        messages += build_turn_messages(self.state, self.memory, player_input)
        messages += [
            {"role": "user", "content": (
                "【系统判定结果（由系统决定，请严格遵守，不得改写结局）】\n" + brief + "\n\n"
                "【本次判定声明】\n" + json.dumps(request, ensure_ascii=False) + "\n\n"
                "【未向玩家发布的草稿，仅供场景参考】\n" + (draft or "（无）") + "\n\n"
                "请根据玩家原行动与系统结果，写出本轮唯一的完整最终叙事。草稿里若提前猜测成败，必须纠正，不能把两个相互矛盾的版本拼接起来。"
                "只写本次行动及直接后果，结束就停；保留玩家目标，例如缴械不等于杀人，逃跑不等于反击。"
                "调用 update_world_state 记录结果后才发生的增量，当前状态中已存在的道具与变更不要重复写入。"
                "生命已经由系统结算，不得修改hp、改写死亡或再次发起combat/judgment。"
                "存活时输出与最终结果匹配的options；死亡时options为空。"
            )},
        ]
        if self.state.is_dead:
            # 死亡与终局状态已由代码确定；模型漏掉工具不能让结局消失。
            text, update = self.llm.generate(messages)
            if not isinstance(update, dict):
                update = {}
            update.setdefault("turn_event", (self.state.data["player"].get("name") or "玩家") + "死亡：" + self.state.data["player"]["death_cause"])
            update["options"] = []
        else:
            text, update = self._call_world_update(messages)
        if not text:
            raise LLMError("判定后未获得叙事，本轮尚未结算，请重试。")
        for key in ("combat", "judgment", "time_block"):
            update.pop(key, None)
        return text or "", update

    # ---- 岁月模式：一段时间的安排 ----
    def _resolve_block(self, req, player_input, draft=None, context_update=None):
        """按旬掷随机事件并结算时间、年龄与休养回血，再让模型把这段日子写出来。"""
        d = self.state.data
        plan = str(req.get("plan") or "日常度日").strip()[:60] or "日常度日"
        days = seasons.clamp_days(req.get("days"))
        seclusion = req.get("seclusion") is True
        known = [n for n, c in (d.get("characters") or {}).items() if isinstance(c, dict)]
        result = seasons.roll_block(days, seclusion, known)

        # 日历与年龄
        calendar = d.setdefault("calendar", {"days": 0})
        before = safe_int(calendar.get("days"), 0)
        calendar["days"] = before + result["elapsed"]
        attrs = d.setdefault("player", {}).setdefault("attributes", {})
        years = calendar["days"] // 360 - before // 360
        if years:
            for key in ("年龄", "age", "岁数"):
                if safe_int(attrs.get(key)) is not None:
                    attrs[key] = safe_int(attrs[key]) + years

        # 休养回血（代码结算，模型不能改）
        max_hp = attrs.get("max_hp") if isinstance(attrs.get("max_hp"), (int, float)) else 100
        hp = min(max_hp, self._get_hp() + seasons.heal_amount(max_hp, result["elapsed"], seclusion))
        attrs["hp"] = hp

        # 被打断且还有剩余时间：记下来，玩家可以继续
        if result["interrupt"] and result["remaining"] > 0:
            d["pending_block"] = {"plan": plan, "remaining": result["remaining"], "seclusion": seclusion}
        else:
            d.pop("pending_block", None)
        d["last_block"] = {"plan": plan, "elapsed": result["elapsed"], "interrupted": bool(result["interrupt"])}

        brief = seasons.block_brief(plan, result)
        text, update = self._generate_block_narrative(brief, player_input, draft)
        merged = merge_updates(context_update or {}, update)
        return text, self._protect_hp(merged, hp)

    def _generate_block_narrative(self, brief, player_input, draft):
        self._refresh_system_prompt()
        messages = [{"role": "system", "content": self.system_prompt}]
        messages += build_turn_messages(self.state, self.memory, player_input)
        messages.append({"role": "user", "content": brief + (
            "\n\n【未向玩家发布的草稿，仅供参考，若与上面的结算矛盾以结算为准】\n" + draft if draft else "")})
        text, update = self._call_world_update(messages)
        if not text:
            raise LLMError("时间安排结算后未获得叙事，本轮尚未结算，请重试。")
        for key in ("combat", "judgment", "time_block"):
            update.pop(key, None)
        return text, update

    # ---- 公开入口 ----
    def start(self):
        """开场：根据初始情境生成开场叙事并初始化状态。"""
        return self._play_turn(OPENING_INSTRUCTION, add_user=False)

    @property
    def is_over(self):
        return self.state.is_dead

    def run_turn(self, player_input):
        if self.is_over:
            return "这段人生已经结束。你可以保存这份记录，或开始新游戏。"
        return self._play_turn(player_input, add_user=True)

    def run_block(self, plan, days, seclusion=False):
        """岁月模式（界面直接发起）：只需一次模型调用。"""
        if self.is_over:
            return "这段人生已经结束。你可以保存这份记录，或开始新游戏。"
        days = seasons.clamp_days(days)
        block = {"plan": plan, "days": days, "seclusion": bool(seclusion)}
        return self._play_turn(seasons.plan_text(plan, days, seclusion), add_user=True, block=block)

    @property
    def pending_block(self):
        return self.state.data.get("pending_block") or None

    def resume_block(self):
        """继续被打断的安排（剩余天数）。"""
        pending = self.pending_block
        if not pending:
            raise ValueError("没有被打断的安排。")
        return self.run_block(pending.get("plan") or "日常度日", pending.get("remaining") or 10,
                              pending.get("seclusion") is True)

    def _play_turn(self, player_input, add_user, block=None):
        state_before = copy.deepcopy(self.state.data)
        memory_before = copy.deepcopy(self.memory.to_dict())
        try:
            if block:
                text, update = self._resolve_block(block, player_input)
                rolled = True
            else:
                text, update = self._generate(player_input)
                rolled = self._has_roll(update)
                text, update = self._handle_rolls(text, update, player_input)
            update = self._guard_vitals(update, rolled)
            self._commit(player_input if add_user else None, text, update, add_user)
            return text
        except Exception:
            # API第二次调用失败时，撤销未完成的扣血、道具消耗和保底计数。
            self.state.data.clear()
            self.state.data.update(state_before)
            restored = Memory.from_dict(memory_before, save_key=self.memory.save_key)
            self.memory.recent = restored.recent
            self.memory.buffer = restored.buffer
            self.memory.turns_since_compression = restored.turns_since_compression
            self._refresh_system_prompt()
            raise

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
