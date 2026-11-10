"""世界状态：结构化 JSON 的读写、摘要生成与增量合并。

这是“结构化状态管理”的核心：一致性不依赖完整对话历史，
而是依赖这份不断被 update_world_state 工具更新的 JSON。
"""
import math
import os
import time

import seasons
from config import SAVE_DIR, MAX_EVENT_LOG, save_slot_suffix, write_json_atomic, read_json_save
from dynamics import RELATION_EVENTS, ATTRIBUTE_EVENTS, effective_affinity, clamp, initial_affinity
from judgment import death_protection_limits, ITEM_MODIFIERS

MAX_OFFSCREEN = 10         # 幕后进展最多保留条数（玩家看不到原文，只列给模型）
RELATION_MAX_DELTA = 30    # P1-10：单轮对同一对象的好感变化上限
MAX_HP_CEILING = 100000    # 生命上限的绝对安全上限（正常玩法远达不到，只防每轮 ×1.5 滚雪球）


class WorldState:
    """封装世界状态的读写与更新。"""

    def __init__(self, data=None, save_key=""):
        self.data = data or self.empty_state()
        self.save_key = save_key or ""

    @staticmethod
    def empty_state():
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        return {
            "meta": {"turn": 0, "created_at": now, "updated_at": now},
            "world": {"name": "", "background": "", "rules": "", "style": ""},
            "player": {
                "name": "",
                "role_description": "",
                "attributes": {},       # 内部数据（hp/max_hp/运气/魅力等），不向玩家显性展示
                "status_effects": [],   # 例如 ["受伤", "疲惫"]
                "talents": [],          # 天赋列表 [{"name","description","tier","drawback"}]，影响判定系数
                "physique": None,       # 体质 {"name","description","tier","drawback"} 或 None（主角构建自选）
                "golden_fingers": [],   # 金手指列表 [{"name","description","tier","drawback"}]（可 1~3 个）
                "life_status": "alive",
            },
            "initial_situation": "",
            "location": {"name": "", "description": ""},
            "time": "",
            "characters": {},           # 角色名 -> {description, attitude_toward_player, location, status, affinity}
            "inventory": [],            # [{"name", "quantity", "description"}]
            "plot_flags": {},           # 关键剧情节点 {"flag": true/"string"}
            "current_goal": "",
            "narrative_summary": "",    # 压缩后的“故事进展摘要”（全局）
            "chapter_summaries": [],    # 章节摘要（每20轮一段，长局记忆，滚动淘汰）
            "event_log": [],            # 最近若干轮的一行事件记录
            "combat": {                 # 战力体系锚定（世界生成时确定）
                "field": "战力",
                "player_power": 10,
                "realm_gap": 5,
                "curve": "realm_gap",
            },
            "last_combat": None,        # 最近一次战斗结果（定性）
            "pity": {},                 # 判定保底计数 {类型: {"fails": n, "successes": n}}
            "custom": {},               # 玩家自定义上传世界的额外字段（保留不丢弃）
            "options": [],              # 本轮结束时给玩家的“显而易见”选项
            "calendar": {"days": 0},    # 开局以来经过的天数（岁月模式按旬累加，由代码结算）
            "offscreen": [],            # 玩家不知道的幕后进展（只给模型看，最多 MAX_OFFSCREEN 条）
            "pending_block": None,      # 被打断且还有剩余的时间安排 {plan, remaining, seclusion}
        }

    # ---- 初始化：应用世界观模板 ----
    def apply_template(self, template):
        """把世界观模板（AI 生成 / 文件上传 / 手动输入）写入状态，并重置记忆相关字段。"""
        d = self.data
        w = template.get("world") or {}
        d["world"]["name"] = w.get("name", "") or ""
        d["world"]["background"] = w.get("background", "") or ""
        d["world"]["rules"] = w.get("rules", "") or ""
        d["world"]["style"] = w.get("style", "") or ""

        p = template.get("player") or {}
        d["player"]["name"] = p.get("name", "") or ""
        d["player"]["role_description"] = p.get("role_description", "") or ""
        d["player"]["attributes"] = dict(p.get("attributes") or {})
        d["player"]["attributes"].setdefault("max_hp", 100)
        if "hp" not in d["player"]["attributes"]:
            d["player"]["attributes"]["hp"] = d["player"]["attributes"]["max_hp"]
        d["player"]["status_effects"] = p.get("status_effects") or []
        d["player"]["life_status"] = "alive"
        d["player"].pop("death_cause", None)
        d["player"].pop("death_protections_used", None)
        d["player"]["talents"] = p.get("talents") or template.get("talents") or []
        d["player"]["physique"] = p.get("physique") or None
        # 金手指：支持新的列表字段 golden_fingers，也兼容旧的单字段 golden_finger
        gfs = p.get("golden_fingers")
        if isinstance(gfs, list):
            d["player"]["golden_fingers"] = gfs
        elif p.get("golden_finger"):
            d["player"]["golden_fingers"] = [p["golden_finger"]]
        else:
            d["player"]["golden_fingers"] = []

        d["initial_situation"] = template.get("initial_situation", "") or ""

        loc = template.get("location") or {}
        d["location"]["name"] = loc.get("name", "") or ""
        d["location"]["description"] = loc.get("description", "") or ""

        d["time"] = template.get("time", "") or ""
        d["characters"] = {}
        for name, info in (template.get("characters") or {}).items():
            if isinstance(name, str) and name.strip():
                char = {}
                if isinstance(info, dict):
                    char = {k: v for k, v in info.items() if isinstance(v, str)}  # 保留模板里的额外文字字段
                    char.update(_sanitize_character(info, trusted=True))
                _ensure_affinity(char, 0)
                d["characters"][name.strip()] = char
        d["inventory"] = template.get("inventory") or []
        d["plot_flags"] = template.get("plot_flags") or {}
        d["current_goal"] = template.get("current_goal", "") or ""

        # 战力体系锚定（缺省用默认值）
        combat = template.get("combat") or {}
        if not isinstance(combat, dict):
            combat = {}
        d["combat"] = {
            "field": combat.get("field", "战力") or "战力",
            "player_power": combat.get("player_power", 10) if isinstance(combat.get("player_power"), (int, float)) else 10,
            "realm_gap": combat.get("realm_gap", 5) if isinstance(combat.get("realm_gap"), (int, float)) else 5,
            "curve": combat.get("curve", "realm_gap") or "realm_gap",
        }

        d["custom"] = template.get("custom") or {}

        # 重置记忆相关字段（新游戏从零开始）
        d["narrative_summary"] = ""
        d["chapter_summaries"] = []
        d["event_log"] = []
        d["last_combat"] = None
        d["pity"] = {}
        self.touch()

    # ---- 持久化 ----
    @property
    def state_path(self):
        return os.path.join(SAVE_DIR, "state" + save_slot_suffix(self.save_key) + ".json")

    def touch(self):
        self.data["meta"]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def save(self):
        self.touch()
        self.data["meta"]["save_key"] = self.save_key
        write_json_atomic(self.state_path, self.data)

    @classmethod
    def load(cls, save_key=""):
        """读档；没有存档返回 None，存档损坏抛 config.SaveCorruptError。"""
        key = save_key or ""
        path = os.path.join(SAVE_DIR, "state" + save_slot_suffix(key) + ".json")
        if not os.path.exists(path):
            return None
        return cls(read_json_save(path), save_key=key)

    # ---- 元信息 ----
    @property
    def is_dead(self):
        p = self.data.get("player") or {}
        hp = (p.get("attributes") or {}).get("hp")
        return p.get("life_status") == "dead" or (isinstance(hp, (int, float)) and hp <= 0)

    def mark_dead(self, cause):
        p = self.data.setdefault("player", {})
        p["life_status"] = "dead"
        p["death_cause"] = p.get("death_cause") or cause or "生命耗尽"
        p.setdefault("attributes", {})["hp"] = 0
        p["status_effects"] = ["死亡"]
        self.data["options"] = []
        self.data["current_goal"] = "本次人生已结束"

    def protection_summary(self):
        player = self.data.get("player") or {}
        used = player.get("death_protections_used") or {}
        return [name + ("：已耗尽，不能再抵挡致命一击" if used.get(name, 0) >= limit else "：尚可抵挡致命一击（剩余" + str(limit - used.get(name, 0)) + "次）")
                for name, limit in death_protection_limits(player).items()]

    def bump_turn(self):
        self.data["meta"]["turn"] += 1

    def add_event(self, event):
        log = self.data["event_log"]
        log.append(event)
        if len(log) > MAX_EVENT_LOG:
            del log[: len(log) - MAX_EVENT_LOG]

    # ---- 生成“当前状态摘要” ----
    def to_summary(self):
        """把结构化状态压缩成一段给模型看的文本摘要。

        每轮发给模型的不是全部历史，而是这份从 JSON 实时生成的摘要。
        """
        d = self.data
        lines = []

        loc = d.get("location", {})
        loc_line = f"- 地点：{loc.get('name') or '未知'}"
        if loc.get("description"):
            loc_line += f"（{loc.get('description')}）"
        lines.append(loc_line)

        if d.get("time"):
            lines.append(f"- 时间：{d['time']}")

        p = d.get("player", {})
        attrs = p.get("attributes") or {}
        attr_str = "、".join(f"{k}={v}" for k, v in attrs.items()) if attrs else "无"
        status = p.get("status_effects") or []
        status_str = "、".join(status) if status else "无"
        lines.append(f"- 玩家：{p.get('name') or '你'}（{p.get('role_description') or '未设定'}）")
        lines.append(f"  属性：{attr_str}；状态：{status_str}")
        if self.is_dead:
            lines.append("  生命状态：已死亡；死因：" + (p.get("death_cause") or "生命耗尽"))

        physique = p.get("physique") or {}
        gf_names = [g.get("name") for g in (p.get("golden_fingers") or []) if isinstance(g, dict) and g.get("name")]
        if physique.get("name") or gf_names:
            lines.append(f"  体质：{physique.get('name') or '无'}；金手指：{'、'.join(gf_names) if gf_names else '无'}")

        chars = d.get("characters") or {}
        turn = d.get("meta", {}).get("turn", 0)
        if chars:
            lines.append(f"- 已知角色（共 {len(chars)} 位）：")
            for name, info in _characters_for_summary(chars, (loc.get("name") or ""), 12):
                bits = []
                if info.get("attitude_toward_player"):
                    bits.append(f"态度={info['attitude_toward_player']}")
                if isinstance(info.get("affinity"), (int, float)):
                    bits.append(f"好感度={effective_affinity(info, turn)}")
                if info.get("location"):
                    bits.append(f"位置={info['location']}")
                if info.get("status"):
                    bits.append(f"状态={info['status']}")
                line = f"  · {name}"
                if info.get("description"):
                    line += f"：{info['description']}"
                if bits:
                    line += "（" + "，".join(bits) + "）"
                lines.append(line)
                memories = [m for m in (info.get("memories") or []) if isinstance(m, str)]
                if memories:
                    lines.append("    往事：" + "；".join(memories[-3:]))
            if len(chars) > 12:
                lines.append("  …（其余角色略）")
        else:
            lines.append("- 已知角色：无")

        inv = d.get("inventory") or []
        if inv:
            item_str, omitted = _summary_items(inv)
            lines.append(f"- 背包：{item_str}")
            if omitted > 0:
                lines.append(f"  …（另有 {omitted} 件未列出）")
        else:
            lines.append("- 背包：空")

        flags = d.get("plot_flags") or {}
        if flags:
            flag_str, omitted = _summary_flags(flags)
            lines.append("- 剧情标记：" + flag_str)
            if omitted > 0:
                lines.append(f"  …（另有 {omitted} 个较早的标记未列出）")

        if d.get("current_goal"):
            lines.append(f"- 当前目标：{d['current_goal']}")

        cal = d.get("calendar") or {}
        days = safe_int(cal.get("days"), 0) or 0
        if days > 0:
            lines.append("- 开局以来已过：" + seasons.span_text(days))
        pend = d.get("pending_block") or {}
        if pend.get("plan"):
            lines.append("- 被打断的安排：" + str(pend["plan"])
                         + "（还剩" + seasons.span_text(safe_int(pend.get("remaining"), 0) or 0)
                         + "，玩家可以选择继续）")
        off = [s for s in (d.get("offscreen") or []) if isinstance(s, str)]
        if off:
            lines.append("- 幕后进展（玩家并不知道，只能通过合理途径逐步透露）：")
            for s in off[-5:]:
                lines.append("  · " + s)

        last_combat = d.get("last_combat")
        if last_combat:
            outcome_cn = {"win": "胜利", "lose": "落败", "flee_success": "成功逃脱", "flee_fail": "逃脱失败"}.get(
                last_combat.get("outcome"), last_combat.get("outcome"))
            lines.append(f"- 最近战斗：对手「{last_combat.get('enemy_name')}」，结果：{outcome_cn}")

        events = d.get("event_log") or []
        if events:
            lines.append("- 近期事件：")
            for i, e in enumerate(events, 1):
                lines.append(f"  {i}. {e}")

        chapters = d.get("chapter_summaries") or []
        if chapters:
            lines.append("- 近期章节摘要：")
            for i, c in enumerate(chapters, 1):
                lines.append(f"  {i}. {c}")

        if d.get("narrative_summary"):
            lines.append(f"- 此前故事摘要：{d['narrative_summary']}")

        return "\n".join(lines)

    # ---- 面向玩家的“去数据化”摘要（不显示 hp/战力/概率等内部数值） ----
    def _hp_band(self):
        if self.is_dead:
            return "死亡"
        attrs = self.data.get("player", {}).get("attributes") or {}
        hp = attrs.get("hp")
        maxhp = attrs.get("max_hp") or 100
        if not isinstance(hp, (int, float)):
            return None
        ratio = hp / max(maxhp, 1)
        if ratio <= 0:
            return "死亡"
        if ratio < 0.25:
            return "重伤"
        if ratio < 0.6:
            return "受伤"
        if ratio < 0.9:
            return "轻伤"
        return "健康"

    def _talent_names(self):
        talents = self.data.get("player", {}).get("talents") or []
        out = []
        for t in talents:
            if isinstance(t, dict):
                out.append(t.get("name") or "")
            else:
                out.append(str(t))
        return [x for x in out if x]

    def to_player_summary(self):
        """给玩家看的定性摘要：不暴露 hp/战力/判定数值等内部数据。"""
        d = self.data
        lines = []

        loc = d.get("location", {})
        loc_line = f"- 地点：{loc.get('name') or '未知'}"
        if loc.get("description"):
            loc_line += f"（{loc.get('description')}）"
        lines.append(loc_line)
        if d.get("time"):
            lines.append(f"- 时间：{d['time']}")

        p = d.get("player", {})
        status = p.get("status_effects") or []
        band = self._hp_band()
        if band:
            status = ([band] + status) if band not in status else status
        status_str = "、".join(status) if status else "状态正常"
        talents = self._talent_names()
        talent_str = "、".join(talents) if talents else "无"
        lines.append(f"- 玩家：{p.get('name') or '你'}（{p.get('role_description') or '未设定'}）")
        lines.append(f"  状态：{status_str}；天赋：{talent_str}")
        if self.is_dead:
            lines.append("  结局：死亡；死因：" + (p.get("death_cause") or "生命耗尽"))
        if self.protection_summary():
            lines.append("  保命能力：" + "；".join(self.protection_summary()))
        physique = p.get("physique") or {}
        gf_names = [g.get("name") for g in (p.get("golden_fingers") or []) if isinstance(g, dict) and g.get("name")]
        if physique.get("name") or gf_names:
            lines.append(f"  体质：{physique.get('name') or '无'}；金手指：{'、'.join(gf_names) if gf_names else '无'}")

        chars = d.get("characters") or {}
        turn = d.get("meta", {}).get("turn", 0)
        if chars:
            lines.append(f"- 已知角色（共 {len(chars)} 位）：")
            for name, info in list(chars.items())[:12]:
                bits = []
                if info.get("attitude_toward_player"):
                    bits.append(f"态度={info['attitude_toward_player']}")
                if isinstance(info.get("affinity"), (int, float)):
                    aff = effective_affinity(info, turn)
                    if aff >= 70:
                        bits.append("好感=高")
                    elif aff >= 40:
                        bits.append("好感=中")
                    elif aff >= 15:
                        bits.append("好感=低")
                    else:
                        bits.append("好感=陌生")
                if info.get("location"):
                    bits.append(f"位置={info['location']}")
                if info.get("status"):
                    bits.append(f"状态={info['status']}")
                line = f"  · {name}"
                if info.get("description"):
                    line += f"：{info['description']}"
                if bits:
                    line += "（" + "，".join(bits) + "）"
                lines.append(line)
            if len(chars) > 12:
                lines.append("  …（其余角色略）")
        else:
            lines.append("- 已知角色：无")

        inv = d.get("inventory") or []
        if inv:
            item_str, omitted = _summary_items(inv)
            lines.append(f"- 背包：{item_str}")
            if omitted > 0:
                lines.append(f"  …（另有 {omitted} 件未列出）")
        else:
            lines.append("- 背包：空")

        flags = d.get("plot_flags") or {}
        if flags:
            flag_str, omitted = _summary_flags(flags)
            lines.append("- 剧情标记：" + flag_str)
            if omitted > 0:
                lines.append(f"  …（另有 {omitted} 个较早的标记未列出）")

        if d.get("current_goal"):
            lines.append(f"- 当前目标：{d['current_goal']}")

        cal = d.get("calendar") or {}
        days = safe_int(cal.get("days"), 0) or 0
        if days > 0:
            lines.append("- 开局以来已过：" + seasons.span_text(days))
        pend = d.get("pending_block") or {}
        if pend.get("plan"):
            lines.append("- 被打断的安排：" + str(pend["plan"])
                         + "（还剩" + seasons.span_text(safe_int(pend.get("remaining"), 0) or 0)
                         + "，玩家可以选择继续）")

        last_combat = d.get("last_combat")
        if last_combat:
            outcome_cn = {"win": "胜利", "lose": "落败", "flee_success": "成功逃脱", "flee_fail": "逃脱失败"}.get(
                last_combat.get("outcome"), last_combat.get("outcome"))
            lines.append(f"- 最近战斗：对手「{last_combat.get('enemy_name')}」，结果：{outcome_cn}")

        events = d.get("event_log") or []
        if events:
            lines.append("- 近期事件：")
            for i, e in enumerate(events, 1):
                lines.append(f"  {i}. {e}")

        chapters = d.get("chapter_summaries") or []
        if chapters:
            lines.append("- 近期章节摘要：")
            for i, c in enumerate(chapters, 1):
                lines.append(f"  {i}. {c}")

        if d.get("narrative_summary"):
            lines.append(f"- 此前故事摘要：{d['narrative_summary']}")

        return "\n".join(lines)


def _characters_for_summary(chars, location_name, limit):
    """摘要里展示哪些角色：先列在当前地点的，再按最近互动倒序补足，避免新认识的人被挤掉。"""
    items = [(n, c) for n, c in chars.items() if isinstance(c, dict)]
    here = [(n, c) for n, c in items if location_name and c.get("location") and
            (c["location"] in location_name or location_name in c["location"])]
    rest = [(n, c) for n, c in reversed(items) if (n, c) not in here]
    return (here + rest)[:limit]


SUMMARY_FLAG_LIMIT = 20   # 摘要里最多列出的剧情标记数
SUMMARY_ITEM_LIMIT = 15   # 摘要里最多列出的背包条目数


def _summary_flags(flags):
    """摘要用的剧情标记：保留**最近更新**的 20 个（更新时 apply_update 会把键移到末尾）。"""
    items = list(flags.items())[-SUMMARY_FLAG_LIMIT:]
    text = "，".join(f"{k}={v}" for k, v in items)
    return text, len(flags) - len(items)


def _summary_items(inventory):
    """摘要用的背包：最近 15 件 + 永远带上 ITEM_MODIFIERS 里的特殊道具，并说明省略件数。"""
    valid = [i for i in inventory if isinstance(i, dict) and i.get("name")]
    recent = valid[-SUMMARY_ITEM_LIMIT:]
    recent_names = {i.get("name") for i in recent}
    special = [i for i in valid if i.get("name") in ITEM_MODIFIERS and i.get("name") not in recent_names]
    shown = special + recent
    text = "、".join(f"{i.get('name')}×{i.get('quantity', 1)}" for i in shown)
    return text, len(valid) - len(shown)


def _sanitize_character(info, trusted=False):
    """清理角色字段类型：态度应为文字、好感度为数字，防止模型把数值误塞进态度等。

    P0-2：`trusted=False`（模型经 update_world_state 写入）时**丢弃**
    `affinity` / `met_turn` / `interactions`——这些是系统独占字段：
    初始好感只能由 `apply_template`（可信模板）或 `relationship_events`（代码换算）写入。
    """
    if not isinstance(info, dict):
        return {}
    out = {}
    if isinstance(info.get("description"), str):
        out["description"] = info["description"]
    if isinstance(info.get("attitude_toward_player"), str):
        out["attitude_toward_player"] = info["attitude_toward_player"]
    if isinstance(info.get("location"), str):
        out["location"] = info["location"]
    if isinstance(info.get("status"), str):
        out["status"] = info["status"]
    if trusted:
        aff = info.get("affinity")
        if isinstance(aff, (int, float)) and not isinstance(aff, bool):
            out["affinity"] = clamp(int(aff), 0, 100)
        for k in ("met_turn", "interactions"):
            v = info.get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                out[k] = v
    if isinstance(info.get("memories"), list):
        out["memories"] = [str(m).strip() for m in info["memories"] if isinstance(m, str) and m.strip()][-MAX_CHARACTER_MEMORIES:]
    return out


MAX_CHARACTER_MEMORIES = 8   # 每个角色最多保留的往事条数（摘要里只带最近 3 条）


def _add_memory(char, memory):
    """给角色追加一条“与玩家的往事”，去重并滚动保留最近 MAX_CHARACTER_MEMORIES 条。"""
    if not isinstance(memory, str) or not memory.strip():
        return
    memory = memory.strip()[:80]
    memories = [m for m in (char.get("memories") or []) if isinstance(m, str)]
    if memory in memories:
        return
    memories.append(memory)
    char["memories"] = memories[-MAX_CHARACTER_MEMORIES:]


def _ensure_affinity(char, turn):
    """角色第一次被记录时，按态度给初始好感度并记下相识回合。"""
    if not isinstance(char.get("affinity"), (int, float)) or isinstance(char.get("affinity"), bool):
        char["affinity"] = initial_affinity(char.get("attitude_toward_player"))
    if not isinstance(char.get("met_turn"), int):
        char["met_turn"] = turn


def safe_int(value, default=None):
    """把数字或数字字符串转成 int；转不了返回 default（模型偶尔会给出“三”“筑基初期”之类）。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value) if value == value and value not in (float("inf"), float("-inf")) else default
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default


def _is_finite_number(value):
    """有限数字（排除 bool / NaN / ±Infinity）——模型可能塞进这些值。"""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _clean_number(value):
    """整数值存成 int，其余保留 3 位小数，避免状态里出现 15.000000000000002。"""
    value = float(value)
    return int(value) if value.is_integer() else round(value, 3)


def _as_item(item):
    """背包条目容错：裸字符串视为 1 个；不是对象/字符串的丢弃。"""
    if isinstance(item, str):
        return {"name": item.strip(), "quantity": 1} if item.strip() else None
    return item if isinstance(item, dict) else None


def _as_str_list(value):
    """状态效果 / 选项：字符串视为单项，列表逐项转字符串，其它丢弃。"""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    return None


def apply_update(state, update, power_cap=None):
    """把模型调用 update_world_state 返回的增量合并进现有状态。

    采用“增量合并”策略：只改动模型明确给出的字段，未给出的保持不变，
    避免模型每次都要复述完整状态而出错。
    对字段类型做容错：格式不对的单项跳过，不让整轮报错。

    P0-2 系统独占字段：`player_power` 单次变化被钳制在 ±power_cap 内
    （默认 ±1 个 realm_gap；岁月模式结算时 game._commit 会传入放宽后的 cap），
    `max_hp` 单次变化被钳制在原值的 0.5×~1.5×，写入后保证 hp <= max_hp。
    """
    if not isinstance(update, dict):
        return state
    d = state.data
    player = d.setdefault("player", {})
    attrs = player.setdefault("attributes", {})
    if not isinstance(d.get("characters"), dict):
        d["characters"] = {}
    if not isinstance(d.get("inventory"), list):
        d["inventory"] = []
    if not isinstance(d.get("plot_flags"), dict):
        d["plot_flags"] = {}
    if not isinstance(d.get("location"), dict):
        d["location"] = {"name": "", "description": ""}

    for key, target in (("location", "name"), ("location_description", "description")):
        if update.get(key) and isinstance(update[key], str):
            d["location"][target] = update[key]
    if update.get("time") and isinstance(update["time"], str):
        d["time"] = update["time"]

    if isinstance(update.get("player_attributes"), dict):
        incoming = dict(update["player_attributes"])
        # hp 是系统独占字段：只接受有限数字，字符串/布尔/NaN/负数一律丢弃或归零
        if "hp" in incoming:
            if _is_finite_number(incoming["hp"]):
                incoming["hp"] = _clean_number(max(0, incoming["hp"]))
            else:
                incoming.pop("hp")
        if "max_hp" in incoming:
            # 生命上限是系统独占字段：单轮只能在原值的 0.5×~1.5× 之间变动（P0-2），
            # 并受绝对安全上限约束，避免「每轮 ×1.5」长期滚成天文数字。
            cur_max = attrs.get("max_hp")
            cur_max = cur_max if _is_finite_number(cur_max) and cur_max >= 1 else 100
            new_max = incoming["max_hp"]
            if _is_finite_number(new_max) and new_max >= 1:
                clamped = min(max(new_max, cur_max * 0.5), cur_max * 1.5)
                incoming["max_hp"] = _clean_number(min(clamped, MAX_HP_CEILING))
            else:
                incoming.pop("max_hp")
        attrs.update(incoming)
    if "player_status_effects" in update:
        effects = _as_str_list(update["player_status_effects"])
        if effects is not None:
            player["status_effects"] = effects

    # 角色：按名字 upsert，字段做类型守卫，缺失字段保留原值
    turn = d.get("meta", {}).get("turn", 0)
    if isinstance(update.get("characters"), dict):
        for name, info in update["characters"].items():
            if not isinstance(name, str) or not name.strip():
                continue
            clean = _sanitize_character(info)
            memory = info.get("memory") if isinstance(info, dict) else None
            if not clean and not (isinstance(memory, str) and memory.strip()):
                continue
            existing = d["characters"].get(name)
            if isinstance(existing, dict) and existing:
                existing.update(clean)
                target = existing
            else:
                target = d["characters"][name] = clean
            _ensure_affinity(target, turn)
            _add_memory(target, memory)
            d["characters"][name] = d["characters"].pop(name)  # 最近互动的角色排到最后，摘要优先展示

    # 背包：新增按名字累加数量
    for raw in update.get("inventory_add") or []:
        item = _as_item(raw)
        if not item or not isinstance(item.get("name"), str) or not item["name"].strip():
            continue
        name = item["name"]
        qty = max(1, safe_int(item.get("quantity"), 1) or 1)
        found = next((i for i in d["inventory"] if isinstance(i, dict) and i.get("name") == name), None)
        if found:
            found["quantity"] = safe_int(found.get("quantity"), 1) + qty
        else:
            d["inventory"].append({
                "name": name,
                "quantity": qty,
                "description": item.get("description", "") if isinstance(item.get("description"), str) else "",
            })

    # 背包：按数量扣减（支持货币）；remove_all 才整类移除
    for raw in update.get("inventory_remove") or []:
        item = _as_item(raw)  # 容错：裸字符串只扣 1，绝不整类删
        if not item or not isinstance(item.get("name"), str) or not item["name"].strip():
            continue
        name = item["name"]
        remove_all = item.get("remove_all") is True
        qty = max(1, safe_int(item.get("quantity"), 1) or 1)
        for entry in d["inventory"]:
            if isinstance(entry, dict) and entry.get("name") == name:
                if remove_all:
                    entry["quantity"] = 0
                else:
                    entry["quantity"] = safe_int(entry.get("quantity"), 1) - qty
    d["inventory"] = [i for i in d["inventory"] if isinstance(i, dict) and safe_int(i.get("quantity"), 1) > 0]

    if isinstance(update.get("plot_flags"), dict):
        # 更新某个标记时把它移到末尾：摘要取最近 20 个，新标记才不会被老旧标记挤掉（P1-9）
        for key, value in update["plot_flags"].items():
            if not isinstance(key, str) or not key.strip():
                continue
            d["plot_flags"].pop(key, None)
            d["plot_flags"][key] = value

    if update.get("current_goal") and isinstance(update["current_goal"], str):
        d["current_goal"] = update["current_goal"]

    # 玩家战力成长（突破/升级）：P0-2 系统独占字段。
    # 必须是有限正数；单次变化被钳制在 ±power_cap（默认 ±1 个 realm_gap）。
    if update.get("player_power") is not None:
        anchor = d.setdefault("combat", {})
        current = anchor.get("player_power")
        current = current if _is_finite_number(current) and current > 0 else 10
        realm_gap = anchor.get("realm_gap")
        realm_gap = realm_gap if _is_finite_number(realm_gap) and realm_gap > 0 else 5
        cap = power_cap if _is_finite_number(power_cap) and power_cap > 0 else realm_gap
        power = update["player_power"]
        if _is_finite_number(power) and power > 0:
            delta = max(-cap, min(cap, power - current))
            anchor["player_power"] = _clean_number(max(current + delta, 0.001))

    # 关系事件：好感度动态增减（相识时长另由 time_bonus 派生）
    # P1-10：同一对象单轮只取绝对值最大的一条事件，单轮好感变化钳制在 ±RELATION_MAX_DELTA。
    best_events = {}
    for ev in update.get("relationship_events") or []:
        if not isinstance(ev, dict):
            continue
        subject = ev.get("subject")
        event = ev.get("event")
        if not isinstance(subject, str) or not subject or event not in RELATION_EVENTS:
            continue
        current = best_events.get(subject)
        if current is None or abs(RELATION_EVENTS[event]) > abs(RELATION_EVENTS[current]):
            best_events[subject] = event
    for subject, event in best_events.items():
        c = d["characters"].get(subject)
        if not isinstance(c, dict):
            c = d["characters"][subject] = {}
        _ensure_affinity(c, turn)
        before = safe_int(c.get("affinity"), 0)
        delta = max(-RELATION_MAX_DELTA, min(RELATION_MAX_DELTA, RELATION_EVENTS[event]))
        c["affinity"] = clamp(before + delta, 0, 100)
        c["interactions"] = safe_int(c.get("interactions"), 0) + 1
        d["characters"][subject] = d["characters"].pop(subject)

    # 属性事件：其它系数（声望/魅力/福缘/体力/修为…）动态增减。
    # 若该属性当前是文字（如 修为="筑基初期"），数值增减没有意义，跳过而不是报错。
    def bump(attr, delta):
        cur = attrs.get(attr, 0)
        cur_int = safe_int(cur)
        if cur_int is None:
            return
        attrs[attr] = cur_int + delta

    for ev in update.get("attribute_events") or []:
        if not isinstance(ev, dict):
            continue
        if "attribute" in ev and "delta" in ev:
            delta = safe_int(ev["delta"])
            if delta is None or not isinstance(ev["attribute"], str):
                continue
            bump(ev["attribute"], delta)
        else:
            deltas = ATTRIBUTE_EVENTS.get(ev.get("event")) or {}
            for attr, delta in deltas.items():
                bump(attr, int(delta))

    # 本轮可选行动（显而易见选项，不含隐藏剧情触发）
    if "options" in update:
        options = _as_str_list(update["options"])
        if options is not None:
            d["options"] = options[:5]

    # 幕后进展：只收字符串，去首尾空格、去重，追加并保留最近 MAX_OFFSCREEN 条。
    # 这是玩家不知道的事（只进状态摘要给模型），绝不出现在 to_player_summary。
    # time_block 不在这里处理 —— 它由 game._resolve_block 走两段式结算。
    if "offscreen" in update:
        items = _as_str_list(update["offscreen"])
        if items is not None:
            log = d.setdefault("offscreen", [])
            known = {s for s in log if isinstance(s, str)}
            for item in items:
                if item not in known:
                    log.append(item)
                    known.add(item)
            if len(log) > MAX_OFFSCREEN:
                del log[: len(log) - MAX_OFFSCREEN]

    if update.get("turn_event"):
        state.add_event(str(update["turn_event"]))

    # 生命值不能超过上限（max_hp 可能被下调）
    max_hp = attrs.get("max_hp")
    if _is_finite_number(max_hp) and max_hp >= 1:
        hp_now = attrs.get("hp")
        if _is_finite_number(hp_now) and hp_now > max_hp:
            attrs["hp"] = _clean_number(max_hp)

    return state
