"""世界状态：结构化 JSON 的读写、摘要生成与增量合并。

这是“结构化状态管理”的核心：一致性不依赖完整对话历史，
而是依赖这份不断被 update_world_state 工具更新的 JSON。
"""
import json
import os
import time

from config import SAVE_DIR, MAX_EVENT_LOG, save_slot_suffix
from dynamics import RELATION_EVENTS, ATTRIBUTE_EVENTS, effective_affinity, clamp


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
        d["characters"] = template.get("characters") or {}
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
        os.makedirs(SAVE_DIR, exist_ok=True)
        self.touch()
        self.data["meta"]["save_key"] = self.save_key
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, save_key=""):
        key = save_key or ""
        path = os.path.join(SAVE_DIR, "state" + save_slot_suffix(key) + ".json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return cls(json.load(f), save_key=key)

    # ---- 元信息 ----
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
            if len(chars) > 12:
                lines.append("  …（其余角色略）")
        else:
            lines.append("- 已知角色：无")

        inv = d.get("inventory") or []
        if inv:
            shown = inv[:15]
            item_str = "、".join(f"{i.get('name')}×{i.get('quantity', 1)}" for i in shown)
            lines.append(f"- 背包：{item_str}")
            if len(inv) > 15:
                lines.append("  …（其余物品略）")
        else:
            lines.append("- 背包：空")

        flags = d.get("plot_flags") or {}
        if flags:
            shown_flags = list(flags.items())[:20]
            lines.append("- 剧情标记：" + "，".join(f"{k}={v}" for k, v in shown_flags))
            if len(flags) > 20:
                lines.append("  …（其余标记略）")

        if d.get("current_goal"):
            lines.append(f"- 当前目标：{d['current_goal']}")

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
        attrs = self.data.get("player", {}).get("attributes") or {}
        hp = attrs.get("hp")
        maxhp = attrs.get("max_hp") or 100
        if not isinstance(hp, (int, float)):
            return None
        ratio = hp / max(maxhp, 1)
        if ratio <= 0:
            return "濒死"
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
            shown = inv[:15]
            item_str = "、".join(f"{i.get('name')}×{i.get('quantity', 1)}" for i in shown)
            lines.append(f"- 背包：{item_str}")
            if len(inv) > 15:
                lines.append("  …（其余物品略）")
        else:
            lines.append("- 背包：空")

        flags = d.get("plot_flags") or {}
        if flags:
            shown_flags = list(flags.items())[:20]
            lines.append("- 剧情标记：" + "，".join(f"{k}={v}" for k, v in shown_flags))
            if len(flags) > 20:
                lines.append("  …（其余标记略）")

        if d.get("current_goal"):
            lines.append(f"- 当前目标：{d['current_goal']}")

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


def _sanitize_character(info):
    """清理角色字段类型：态度应为文字、好感度为数字，防止模型把数值误塞进态度等。"""
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
    aff = info.get("affinity")
    if isinstance(aff, (int, float)) and not isinstance(aff, bool):
        out["affinity"] = int(aff)
    for k in ("met_turn", "interactions"):
        v = info.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
    return out


def apply_update(state, update):
    """把模型调用 update_world_state 返回的增量合并进现有状态。

    采用“增量合并”策略：只改动模型明确给出的字段，未给出的保持不变，
    避免模型每次都要复述完整状态而出错。
    """
    d = state.data

    if update.get("location"):
        d["location"]["name"] = update["location"]
    if update.get("location_description"):
        d["location"]["description"] = update["location_description"]
    if update.get("time"):
        d["time"] = update["time"]

    if update.get("player_attributes"):
        d["player"]["attributes"].update(update["player_attributes"])
    if "player_status_effects" in update and update["player_status_effects"] is not None:
        d["player"]["status_effects"] = list(update["player_status_effects"])

    # 角色：按名字 upsert，字段做类型守卫，缺失字段保留原值
    if update.get("characters"):
        for name, info in update["characters"].items():
            clean = _sanitize_character(info)
            if not clean:
                continue
            existing = d["characters"].get(name)
            if existing:
                existing.update(clean)
            else:
                d["characters"][name] = clean

    # 背包：新增按名字累加数量
    for item in update.get("inventory_add") or []:
        name = item.get("name")
        if not name:
            continue
        qty = int(item.get("quantity", 1) or 1)
        found = next((i for i in d["inventory"] if i.get("name") == name), None)
        if found:
            found["quantity"] = int(found.get("quantity", 1)) + qty
        else:
            d["inventory"].append({
                "name": name,
                "quantity": qty,
                "description": item.get("description", ""),
            })

    # 背包：按数量扣减（支持货币）；remove_all 才整类移除
    for item in update.get("inventory_remove") or []:
        if isinstance(item, str):
            item = {"name": item, "quantity": 1}  # 容错：裸字符串只扣 1，绝不整类删
        name = item.get("name")
        if not name:
            continue
        remove_all = bool(item.get("remove_all"))
        qty = int(item.get("quantity", 1) or 1)
        for entry in d["inventory"]:
            if entry.get("name") == name:
                if remove_all:
                    entry["quantity"] = 0
                else:
                    entry["quantity"] = int(entry.get("quantity", 1)) - qty
    d["inventory"] = [i for i in d["inventory"] if int(i.get("quantity", 0)) > 0]

    if update.get("plot_flags"):
        d["plot_flags"].update(update["plot_flags"])

    if update.get("current_goal"):
        d["current_goal"] = update["current_goal"]

    # 玩家战力成长（突破/升级）
    if "player_power" in update and isinstance(update["player_power"], (int, float)):
        d.setdefault("combat", {})["player_power"] = update["player_power"]

    # 关系事件：好感度动态增减（相识时长另由 time_bonus 派生）
    for ev in update.get("relationship_events") or []:
        if not isinstance(ev, dict):
            continue
        subject = ev.get("subject")
        event = ev.get("event")
        if not subject or event not in RELATION_EVENTS:
            continue
        c = d["characters"].setdefault(subject, {})
        c["affinity"] = clamp(int(c.get("affinity", 0)) + RELATION_EVENTS[event], 0, 100)
        c["interactions"] = int(c.get("interactions", 0)) + 1
        if "met_turn" not in c:
            c["met_turn"] = d["meta"]["turn"]

    # 属性事件：其它系数（声望/魅力/福缘/体力/修为…）动态增减
    for ev in update.get("attribute_events") or []:
        if not isinstance(ev, dict):
            continue
        if "attribute" in ev and "delta" in ev:
            attr = ev["attribute"]
            try:
                delta = int(ev["delta"])
            except (TypeError, ValueError):
                continue
            d["player"]["attributes"][attr] = int(d["player"]["attributes"].get(attr, 0)) + delta
        else:
            deltas = ATTRIBUTE_EVENTS.get(ev.get("event")) or {}
            for attr, delta in deltas.items():
                d["player"]["attributes"][attr] = int(d["player"]["attributes"].get(attr, 0)) + int(delta)

    # 本轮可选行动（显而易见选项，不含隐藏剧情触发）
    if "options" in update and isinstance(update.get("options"), list):
        d["options"] = [str(o) for o in update["options"] if str(o).strip()][:5]

    if update.get("turn_event"):
        state.add_event(update["turn_event"])

    return state
