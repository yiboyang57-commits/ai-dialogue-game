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

# 等级别名归一化：LLM 可能返回“金色/传说/史诗”等，统一映射到标准等级
_TIER_ALIASES = {
    "白": "白", "白色": "白", "普通": "白", "平凡": "白",
    "绿": "绿", "绿色": "绿", "优良": "绿",
    "蓝": "蓝", "蓝色": "蓝", "稀有": "蓝",
    "紫": "紫", "紫色": "紫", "史诗": "紫",
    "金": "金", "金色": "金", "传说": "金",
    "红": "红", "红色": "红", "神话": "红",
    "炫彩": "炫彩", "彩色": "炫彩", "彩虹": "炫彩", "彩": "炫彩",
}


def _canon_tier(tier):
    if tier in TIERS:
        return tier
    return _TIER_ALIASES.get(str(tier).strip(), "蓝")


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
        "drawback": {"type": "string", "description": "金/红/炫彩等高等级项必填的代价或负面效果；低等级可留空。"},
    },
    "required": ["name", "tier"],
}

GENERATE_POOL_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_character_pool",
        "description": (
            "根据给定世界观，生成一批该世界风格的『天赋 / 体质 / 金手指』候选池。"
            "每个候选含 name、description、tier、drawback（drawback 仅高等级需要）。"
            "要求：名字必须原创、贴合世界观题材，不要复用任何内置预设名（如天命之子、先天道体、随身系统等）；"
            "等级分布以『金、紫』为最多（峰值在金紫之间），蓝次之，红、绿再次，白较少，炫彩极稀有；"
            "名字强弱与等级一致。每个类别至少包含 1 个炫彩、2 个红、3 个金（越多越好、越多样越好），"
            "且这些金/红/炫彩项都要写 drawback（代价/负面效果），越逆天的能力代价越沉重，避免变成无代价的爽游。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "talents": {"type": "array", "description": "天赋候选，约 50 个：白约3、绿约5、蓝约9、紫约14、金约13、红约5、炫彩约2。", "items": _POOL_ITEM_SCHEMA},
                "physiques": {"type": "array", "description": "体质候选，约 25 个：白约2、绿约3、蓝约5、紫约6、金约6、红约2、炫彩约1。", "items": _POOL_ITEM_SCHEMA},
                "golden_fingers": {"type": "array", "description": "金手指候选，约 30 个：白约2、绿约3、蓝约6、紫约8、金约8、红约3、炫彩约1。", "items": _POOL_ITEM_SCHEMA},
            },
            "required": ["talents", "physiques", "golden_fingers"],
        },
    },
}

# 每个候选池必须保底的高等级数量（安全底线，兜底表可覆盖）
_POOL_FLOOR = {"炫彩": 1, "红": 2, "金": 3}

# 高等级项若 LLM 漏写代价，兜底补一句通用代价
_GENERIC_DRAWBACKS = {
    "金": "强大的能力伴随相应代价，不可无节制使用。",
    "红": "逆天之力会反噬自身，使用时需付出沉重代价。",
    "炫彩": "至高之力为天地所忌，伴随难以承受的代价与厄运。",
}

# 本地兜底的高等级候选（LLM 未满足保底时补足；每个类别都备足 3金+2红+1炫彩）
_LEGENDARY_SUPPLEMENT = {
    "talents": [
        {"name": "天命所归", "description": "气运极盛，处处逢机缘。", "tier": "金", "drawback": "树大招风，易招强敌觊觎。"},
        {"name": "万法皆通", "description": "任何技艺一看就会、一学就精。", "tier": "金", "drawback": "心魔易生，突破时易走火入魔。"},
        {"name": "皇极霸世", "description": "统领万物的王者之姿。", "tier": "金", "drawback": "孤家寡人，亲近之人多灾厄。"},
        {"name": "洞悉天机", "description": "能窥见未来吉凶、料敌先机。", "tier": "红", "drawback": "窥探天机过多，寿元受损、时常头痛欲裂。"},
        {"name": "一念成魔", "description": "绝境中爆发出恐怖力量。", "tier": "红", "drawback": "爆发后陷入虚弱，甚至短暂失控。"},
        {"name": "混沌主宰", "description": "近乎全能的先天之力。", "tier": "炫彩", "drawback": "为天道所忌，天劫与厄运如影随形。"},
    ],
    "physiques": [
        {"name": "不灭金身", "description": "肉身近乎不灭。", "tier": "金", "drawback": "行动迟缓，难以施展灵巧的身法。"},
        {"name": "万象圣体", "description": "兼容万法的宝体。", "tier": "金", "drawback": "修炼资源消耗惊人，寻常机缘难以满足。"},
        {"name": "天罡战体", "description": "为战而生的强横肉身。", "tier": "金", "drawback": "极易卷入杀伐，难以平静度日。"},
        {"name": "万古神体", "description": "肉身无双、资质绝顶。", "tier": "红", "drawback": "气血过盛，情绪易怒难控。"},
        {"name": "九死涅槃体", "description": "濒死时能涅槃重生。", "tier": "红", "drawback": "每次涅槃都要付出记忆或情感为代价。"},
        {"name": "混沌圣体", "description": "承载万象的至高之躯。", "tier": "炫彩", "drawback": "树大招风，体质反噬时痛不欲生。"},
    ],
    "golden_fingers": [
        {"name": "许愿系统", "description": "可许愿实现愿望。", "tier": "金", "drawback": "每次许愿都要付出等价的代价。"},
        {"name": "因果律商店", "description": "能用代价换取任何结果。", "tier": "金", "drawback": "标价往往是不可逆之物。"},
        {"name": "随身洞天", "description": "自成一界的随身空间。", "tier": "金", "drawback": "洞天成长需要吞噬大量资源。"},
        {"name": "时间回溯", "description": "可回溯时间重来。", "tier": "红", "drawback": "每用一次寿命大减，且可能引发不可预知的蝴蝶效应。"},
        {"name": "死亡回归", "description": "死亡后能回到过去的关键节点。", "tier": "红", "drawback": "回归会失去部分记忆，且死亡时的痛苦会累积。"},
        {"name": "万能商店", "description": "能买到任何东西。", "tier": "炫彩", "drawback": "支付的是寿命、灵魂等无法再生之物。"},
    ],
}


def _normalize_pool(args):
    """校验并清洗 LLM 返回的候选池，去掉非法条目，高等级漏写代价时补通用代价。"""
    out = {"talents": [], "physiques": [], "golden_fingers": []}
    for key in out:
        for it in (args.get(key) or []):
            if not isinstance(it, dict):
                continue
            name = (it.get("name") or "").strip()
            if not name:
                continue
            tier = _canon_tier(it.get("tier"))
            drawback = (it.get("drawback") or "").strip()
            if not drawback and tier in _GENERIC_DRAWBACKS:
                drawback = _GENERIC_DRAWBACKS[tier]
            out[key].append({
                "name": name,
                "description": (it.get("description") or "").strip(),
                "tier": tier,
                "drawback": drawback,
            })
    return out


def _ensure_floor(pool):
    """保证每个候选池至少有 1炫彩/2红/3金；不足则用本地兜底表补足。"""
    for key in ("talents", "physiques", "golden_fingers"):
        items = pool.setdefault(key, [])
        counts = {}
        for it in items:
            counts[it.get("tier")] = counts.get(it.get("tier"), 0) + 1
        for tier, need in _POOL_FLOOR.items():
            have = counts.get(tier, 0)
            if have >= need:
                continue
            for sup in _LEGENDARY_SUPPLEMENT.get(key, []):
                if sup["tier"] != tier:
                    continue
                if any(x["name"] == sup["name"] for x in items):
                    continue
                items.append(dict(sup))
                have += 1
                if have >= need:
                    break
    return pool


_PRESET_TALENT_TIERS = {
    "天命之子": "金", "福星高照": "金", "魅惑天成": "紫", "巧舌如簧": "蓝",
    "神机妙算": "紫", "身手敏捷": "绿", "百战之躯": "蓝", "炼器天才": "蓝", "气运加身": "金",
}
_PRESET_PHYSIQUE_TIERS = {
    "均衡之躯": "白", "先天道体": "金", "纯阳之体": "蓝", "玄阴之体": "紫",
    "霸绝武体": "金", "万毒不侵": "紫",
}
_PRESET_GF_TIERS = {
    "随身系统": "蓝", "天机推演": "金", "存档读档": "红", "天命气运": "金", "点石成金": "紫",
}

# 兜底池额外补充的金手指（让金手指候选足够多、roll 有变化）
_EXTRA_GOLDEN_FINGERS = [
    {"name": "签到系统", "description": "每日签到得奖励。", "tier": "蓝", "drawback": ""},
    {"name": "任务指引", "description": "自动提示下一步关键线索。", "tier": "绿", "drawback": ""},
    {"name": "自动拾取", "description": "自动收集掉落物。", "tier": "蓝", "drawback": ""},
    {"name": "图鉴鉴定", "description": "记录并鉴定所见之物。", "tier": "蓝", "drawback": ""},
    {"name": "技能树系统", "description": "自由加点强化能力。", "tier": "紫", "drawback": ""},
    {"name": "成就系统", "description": "达成成就获得奖励。", "tier": "紫", "drawback": ""},
    {"name": "灵宠系统", "description": "契约并培养宠物。", "tier": "紫", "drawback": ""},
    {"name": "交易市场", "description": "跨世界买卖物资。", "tier": "紫", "drawback": "需缴纳高昂手续费。"},
    {"name": "气运雷达", "description": "感知机缘与危险方位。", "tier": "金", "drawback": "频繁使用会被大能反追踪。"},
    {"name": "全知图书馆", "description": "查询一切知识与情报。", "tier": "红", "drawback": "过度使用会信息过载、精神崩溃。"},
    {"name": "重生回档", "description": "死亡后回到过去节点。", "tier": "红", "drawback": "每次回档会失去部分记忆。"},
    {"name": "创世编辑器", "description": "改写世界底层规则。", "tier": "炫彩", "drawback": "每次改动都引发无法预料的连锁反应。"},
]


def _fallback_pool():
    """LLM 生成失败时，用内置预设凑一个候选池（保底）。"""
    gfs = [
        {"name": g["name"], "description": g["description"], "tier": _PRESET_GF_TIERS.get(g["name"], "蓝"), "drawback": ""}
        for g in GOLDEN_FINGERS if g["name"]
    ]
    for extra in _EXTRA_GOLDEN_FINGERS:
        if not any(x["name"] == extra["name"] for x in gfs):
            gfs.append(dict(extra))
    return {
        "talents": [
            {"name": t["name"], "description": t["description"], "tier": _PRESET_TALENT_TIERS.get(t["name"], "蓝")}
            for t in TALENTS
        ],
        "physiques": [
            {"name": p["name"], "description": p["description"], "tier": _PRESET_PHYSIQUE_TIERS.get(p["name"], "蓝")}
            for p in PHYSIQUES
        ],
        "golden_fingers": gfs,
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
        _content, name, args = llm.call_tool(msgs, GENERATE_POOL_TOOL, temperature=0.9, max_tokens=4096)
        if name == "generate_character_pool" and isinstance(args, dict):
            pool = _normalize_pool(args)
            if pool["talents"] or pool["physiques"] or pool["golden_fingers"]:
                return _ensure_floor(pool)
    except Exception:
        pass
    return _ensure_floor(_fallback_pool())


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
    """把主角构建结果渲染成一段可读摘要。golden_finger_name 可为单个名或列表。"""
    gfs = golden_finger_name
    if isinstance(gfs, str):
        gfs = [gfs] if gfs and gfs != "无" else []
    elif not isinstance(gfs, (list, tuple)):
        gfs = []
    gf_display = "、".join([g for g in gfs if g and g != "无"]) or "无"
    lines = [
        "天赋：" + ("、".join(talent_names_list) if talent_names_list else "无"),
        "体质：" + (physique_name or "无"),
        "金手指：" + gf_display,
    ]
    return "\n".join(lines)


def build_player(template, talent_names_list=None, physique_name=None, golden_finger_name=None,
                 descriptions=None, tiers=None, drawbacks=None):
    """把主角构建选择合并进世界观模板，返回新模板（原模板不被修改）。

    - talent_names_list: 自选天赋名列表（0~MAX_TALENTS 个），覆盖模板自带的天赋。
    - physique_name: 体质名（对应 PHYSIQUES，或自定义名字）。
    - golden_finger_name: 金手指名（对应 GOLDEN_FINGERS；None/「无」表示不带，或自定义名字）。
    - descriptions: {名字: 描述}，用于给自定义项补一句说明；预设项自动用目录里的描述。
    - tiers: {名字: 等级}，等级会写入条目并影响判定强度。
    - drawbacks: {名字: 负面效果/代价}，会写入条目并注入系统提示。
    """
    descs = descriptions or {}
    tier_map = tiers or {}
    drawback_map = drawbacks or {}
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
        talents.append({
            "name": name, "description": desc,
            "tier": tier_map.get(name), "drawback": drawback_map.get(name, ""),
        })
    p["talents"] = talents

    # 体质
    p.pop("physique", None)
    physique = _find(PHYSIQUES, physique_name)
    if physique_name:
        pdesc = physique["description"] if physique else descs.get(physique_name, "")
        p["physique"] = {
            "name": physique_name, "description": pdesc,
            "tier": tier_map.get(physique_name), "drawback": drawback_map.get(physique_name, ""),
        }
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

    # 金手指（支持 1~3 个：golden_finger_name 可为单个名或列表）
    gf_names = golden_finger_name
    if isinstance(gf_names, str):
        gf_names = [gf_names] if gf_names and gf_names != "无" else []
    elif not isinstance(gf_names, (list, tuple)):
        gf_names = []
    gf_names = [g for g in gf_names if g and g != "无"]

    p.pop("golden_finger", None)
    gfs = []
    for gname in gf_names:
        gf = _find(GOLDEN_FINGERS, gname)
        gdesc = gf["description"] if gf else descs.get(gname, "")
        gfs.append({
            "name": gname, "description": gdesc,
            "tier": tier_map.get(gname), "drawback": drawback_map.get(gname, ""),
        })
    p["golden_fingers"] = gfs

    return t
