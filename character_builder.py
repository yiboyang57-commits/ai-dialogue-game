"""主角构建：开局自选天赋 / 体质 / 金手指。

在选定世界观之后、正式开局之前，让玩家构建主角的基本信息。
本文件集中定义三类可选项与合并逻辑：

- 天赋(TALENTS)：自选 0~3 个，名称与 judgment.TALENT_MODIFIERS 一一对应，影响判定成功率。
- 体质(PHYSIQUES)：单选，可带来属性（如生命上限）、战力或判定加成。
- 金手指(GOLDEN_FINGERS)：单选，是主角独有的“外挂/优势”，通常带来全局或分类型判定加成。

build_player() 把选择合并进世界观模板，返回新模板（不修改原模板）。
"""
import copy
import random


# ---------------------------------------------------------------- 天赋等级
# 从低到高：白 < 绿 < 蓝 < 紫 < 金 < 红 < 炫彩
TIERS = ["白", "绿", "蓝", "紫", "金", "红", "炫彩"]

# 用于 UI 展示的颜色（fg=文字/描边，bg=卡片底，border=边框），已适配暖米色背景
TIER_STYLES = {
    "白":   {"fg": "#7a7a7a", "bg": "#fdfdf9", "border": "#d9d4c8"},
    "绿":   {"fg": "#3c7a3c", "bg": "#eaf4e8", "border": "#bfd8bb"},
    "蓝":   {"fg": "#2f6db3", "bg": "#e8f0f8", "border": "#c2d6ec"},
    "紫":   {"fg": "#7a4fd0", "bg": "#efe8f8", "border": "#d6c7ee"},
    "金":   {"fg": "#b8860b", "bg": "#faf0d8", "border": "#e8d5a0"},
    "红":   {"fg": "#c0392b", "bg": "#fae6e2", "border": "#eebfb6"},
    "炫彩": {"fg": "#8e44ad", "bg": "#f6ecfa", "border": "#d9a8e8"},
}


# ---------------------------------------------------------------- 天赋
# 名称需与 judgment.TALENT_MODIFIERS 中的键一一对应，才能生效。
TALENTS = [
    {"name": "天命之子", "description": "气运眷顾，关键时刻常有转机；寻宝、情缘、冒险皆更顺遂。"},
    {"name": "福星高照", "description": "寻宝时运气极佳，常有意外之喜。"},
    {"name": "魅惑天成", "description": "天生魅力，容易博得他人好感与信任。"},
    {"name": "巧舌如簧", "description": "能言善辩，说服、谈判时更占上风。"},
    {"name": "神机妙算", "description": "洞察入微，推理、识破诡计更得心应手。"},
    {"name": "身手敏捷", "description": "身法灵活，潜行与脱身更占优势。"},
    {"name": "百战之躯", "description": "久经沙场，战斗与逃命更游刃有余。"},
    {"name": "炼器天才", "description": "锻造、炼制成功率高人一筹。"},
    {"name": "气运加身", "description": "整体运势略优于常人，诸事顺遂。"},
]

# 可选天赋数量上限
MAX_TALENTS = 3


# ---------------------------------------------------------------- 体质
# attributes：开局写进玩家属性（绝对值，如生命上限）；player_power_delta：初始战力加成。
# 判定加成见 judgment.TALENT_MODIFIERS 中以体质名为键的条目。
PHYSIQUES = [
    {
        "name": "均衡之躯",
        "description": "资质平平但根基扎实，无特殊加成也无短板，最稳健的选择。",
    },
    {
        "name": "先天道体",
        "description": "悟性卓绝，修行/学习事半功倍，对机缘与玄机感知敏锐。",
    },
    {
        "name": "纯阳之体",
        "description": "气血旺盛、生命绵长，持久战中更占优势。",
        "attributes": {"max_hp": 130},
    },
    {
        "name": "玄阴之体",
        "description": "阴柔内敛、感知敏锐，善于察言观色与隐匿行踪。",
    },
    {
        "name": "霸绝武体",
        "description": "天生神力、肉身强横，近身搏杀占尽上风。",
        "player_power_delta": 3,
    },
    {
        "name": "万毒不侵",
        "description": "百毒不侵，对负面状态抵抗力极强，炼药制毒亦有天赋。",
    },
]


# ---------------------------------------------------------------- 金手指
GOLDEN_FINGERS = [
    {"name": None, "description": "不携带金手指，全靠自身实力闯荡。"},
    {"name": "随身系统", "description": "脑海中有一个神秘系统，能发布任务、提供提示与奖励。"},
    {"name": "天机推演", "description": "能在关键抉择前窥见一丝未来的吉凶。"},
    {"name": "存档读档", "description": "危急时刻可以「回档」重来，人生得以反复试错。"},
    {"name": "天命气运", "description": "被大气运所钟，逢凶化吉，机缘不断。"},
    {"name": "点石成金", "description": "点石成金，财富与资源取之不尽。"},
]


# ---------------------------------------------------------------- 工具函数

def _find(catalog, name):
    if not name:
        return None
    for entry in catalog:
        if entry.get("name") == name:
            return entry
    return None


def talent_names():
    return [t["name"] for t in TALENTS]


def physique_names():
    return [p["name"] for p in PHYSIQUES]


def golden_finger_labels():
    """返回带「无」的展示标签列表，与 GOLDEN_FINGERS 顺序一致。"""
    return [g["name"] or "无" for g in GOLDEN_FINGERS]


def golden_finger_name_from_label(label):
    """把「无」这类展示标签还原成名字（None 表示不带）。"""
    if not label or label == "无":
        return None
    return label


# ---------------------------------------------------------------- 随机 roll

def roll_talents():
    """随机 1~MAX_TALENTS 个天赋名。"""
    n = random.randint(1, MAX_TALENTS)
    return random.sample(talent_names(), n)


def roll_physique():
    """随机一个体质名。"""
    return random.choice(physique_names())


def roll_golden_finger():
    """随机一个金手指名（可能为 None = 无）。"""
    return random.choice([g["name"] for g in GOLDEN_FINGERS])


def roll_build():
    """随机一整套构建，返回 (天赋列表, 体质名, 金手指名)。"""
    return roll_talents(), roll_physique(), roll_golden_finger()


# ---------------------------------------------------------------- 候选池生成（按世界观）

_POOL_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "候选名字，贴合世界观题材。"},
        "description": {"type": "string", "description": "一句话效果说明。"},
        "tier": {"type": "string", "enum": TIERS, "description": "等级，白最弱、炫彩最强。"},
    },
    "required": ["name", "tier"],
}

GENERATE_POOL_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_character_pool",
        "description": (
            "根据给定世界观，生成一批该世界风格的『天赋 / 体质 / 金手指』候选池。"
            "每个候选含 name、description、tier。"
            "要求：名字贴合世界观题材；等级整体服从峰值在蓝色的正态分布"
            "（蓝最多、绿紫次之、白更少、金更少、红很少、炫彩极稀有）；名字强弱与等级一致。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "talents": {"type": "array", "description": "天赋候选，约 40 个。", "items": _POOL_ITEM_SCHEMA},
                "physiques": {"type": "array", "description": "体质候选，约 18 个。", "items": _POOL_ITEM_SCHEMA},
                "golden_fingers": {"type": "array", "description": "金手指候选，约 14 个。", "items": _POOL_ITEM_SCHEMA},
            },
            "required": ["talents", "physiques", "golden_fingers"],
        },
    },
}


def _normalize_pool(args):
    """校验并清洗 LLM 返回的候选池，去掉非法条目。"""
    out = {"talents": [], "physiques": [], "golden_fingers": []}
    for key in out:
        for it in (args.get(key) or []):
            if not isinstance(it, dict):
                continue
            name = (it.get("name") or "").strip()
            if not name:
                continue
            tier = it.get("tier") if it.get("tier") in TIERS else "蓝"
            out[key].append({
                "name": name,
                "description": (it.get("description") or "").strip(),
                "tier": tier,
            })
    return out


_PRESET_TALENT_TIERS = {
    "天命之子": "金", "福星高照": "紫", "魅惑天成": "紫", "巧舌如簧": "蓝",
    "神机妙算": "蓝", "身手敏捷": "绿", "百战之躯": "蓝", "炼器天才": "蓝", "气运加身": "紫",
}
_PRESET_PHYSIQUE_TIERS = {
    "均衡之躯": "白", "先天道体": "紫", "纯阳之体": "蓝", "玄阴之体": "蓝",
    "霸绝武体": "金", "万毒不侵": "紫",
}
_PRESET_GF_TIERS = {
    "随身系统": "蓝", "天机推演": "紫", "存档读档": "红", "天命气运": "金", "点石成金": "蓝",
}


def _fallback_pool():
    """LLM 生成失败时，用内置预设凑一个候选池（保底）。"""
    return {
        "talents": [
            {"name": t["name"], "description": t["description"], "tier": _PRESET_TALENT_TIERS.get(t["name"], "蓝")}
            for t in TALENTS
        ],
        "physiques": [
            {"name": p["name"], "description": p["description"], "tier": _PRESET_PHYSIQUE_TIERS.get(p["name"], "蓝")}
            for p in PHYSIQUES
        ],
        "golden_fingers": [
            {"name": g["name"], "description": g["description"], "tier": _PRESET_GF_TIERS.get(g["name"], "蓝")}
            for g in GOLDEN_FINGERS if g["name"]
        ],
    }


def generate_pool(llm, template):
    """调用 LLM 按世界观生成候选池；失败则回退到内置预设池。"""
    w = (template.get("world") or {}) if isinstance(template, dict) else {}
    combat = (template.get("combat") or {}) if isinstance(template, dict) else {}
    bg = w.get("background") or ""
    rules = w.get("rules") or ""
    field = combat.get("field") or "战力"
    msgs = [
        {"role": "system", "content": "你是文字冒险游戏的设定生成器，只输出结构化的角色候选池，不输出多余文字。"},
        {"role": "user", "content": (
            "请为下面的世界观生成候选池，并调用 generate_character_pool 返回结果。\n\n"
            f"世界观背景：{bg}\n世界规则：{rules}\n战力量纲：{field}\n"
        )},
    ]
    try:
        _content, name, args = llm.call_tool(msgs, GENERATE_POOL_TOOL, temperature=0.9)
        if name == "generate_character_pool" and isinstance(args, dict):
            pool = _normalize_pool(args)
            if pool["talents"] or pool["physiques"] or pool["golden_fingers"]:
                return pool
    except Exception:
        pass
    return _fallback_pool()


def sample_hand(pool_items, n=9, locked=None):
    """从候选池随机抽 n 个；locked 若存在则保留在结果里（其余仍随机）。"""
    items = list(pool_items or [])
    if not items:
        return []
    locked_item = next((x for x in items if x.get("name") == locked), None) if locked else None
    others = [x for x in items if x.get("name") != locked]
    n = max(1, min(n, len(items)))
    if locked_item is not None:
        rest = random.sample(others, min(n - 1, len(others)))
        hand = [locked_item] + rest
    else:
        hand = random.sample(items, n)
    return hand


def summarize_build(talent_names_list, physique_name, golden_finger_name):
    """把主角构建结果渲染成一段可读摘要。"""
    lines = [
        "天赋：" + ("、".join(talent_names_list) if talent_names_list else "无"),
        "体质：" + (physique_name or "无"),
        "金手指：" + (golden_finger_name or "无"),
    ]
    return "\n".join(lines)


def build_player(template, talent_names_list=None, physique_name=None, golden_finger_name=None,
                 descriptions=None, tiers=None):
    """把主角构建选择合并进世界观模板，返回新模板（原模板不被修改）。

    - talent_names_list: 自选天赋名列表（0~MAX_TALENTS 个），覆盖模板自带的天赋。
    - physique_name: 体质名（对应 PHYSIQUES，或自定义名字）。
    - golden_finger_name: 金手指名（对应 GOLDEN_FINGERS；None/「无」表示不带，或自定义名字）。
    - descriptions: {名字: 描述}，用于给自定义项补一句说明；预设项自动用目录里的描述。
    - tiers: {名字: 等级}，等级会写入条目并影响判定强度。
    """
    descs = descriptions or {}
    tier_map = tiers or {}
    t = copy.deepcopy(template)
    p = t.setdefault("player", {})
    if not isinstance(p, dict):
        p = {}
        t["player"] = p

    # 自选天赋（覆盖模板自带天赋）
    talents = []
    for name in (talent_names_list or []):
        entry = _find(TALENTS, name)
        desc = entry["description"] if entry else descs.get(name, "")
        talents.append({"name": name, "description": desc, "tier": tier_map.get(name)})
    p["talents"] = talents

    # 体质
    p.pop("physique", None)
    physique = _find(PHYSIQUES, physique_name)
    if physique_name:
        pdesc = physique["description"] if physique else descs.get(physique_name, "")
        p["physique"] = {"name": physique_name, "description": pdesc, "tier": tier_map.get(physique_name)}
        if physique:
            # 预设体质才应用属性/战力加成
            attrs = p.get("attributes")
            if not isinstance(attrs, dict):
                attrs = {}
                p["attributes"] = attrs
            for k, v in (physique.get("attributes") or {}).items():
                attrs[k] = v
            if "max_hp" in (physique.get("attributes") or {}):
                attrs["hp"] = attrs["max_hp"]  # 新角色开局满血
            delta = physique.get("player_power_delta")
            if isinstance(delta, (int, float)):
                c = t.get("combat")
                if not isinstance(c, dict):
                    c = {}
                    t["combat"] = c
                base = c.get("player_power", 10) if isinstance(c.get("player_power"), (int, float)) else 10
                c["player_power"] = base + delta

    # 金手指
    p.pop("golden_finger", None)
    if golden_finger_name and golden_finger_name != "无":
        gf = _find(GOLDEN_FINGERS, golden_finger_name)
        gdesc = gf["description"] if gf else descs.get(golden_finger_name, "")
        p["golden_finger"] = {"name": golden_finger_name, "description": gdesc, "tier": tier_map.get(golden_finger_name)}

    return t
