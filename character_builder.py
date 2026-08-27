"""主角构建：开局自选天赋 / 体质 / 金手指。

在选定世界观之后、正式开局之前，让玩家构建主角的基本信息。
本文件集中定义三类可选项与合并逻辑：

- 天赋(TALENTS)：自选 0~3 个，名称与 judgment.TALENT_MODIFIERS 一一对应，影响判定成功率。
- 体质(PHYSIQUES)：单选，可带来属性（如生命上限）、战力或判定加成。
- 金手指(GOLDEN_FINGERS)：单选，是主角独有的“外挂/优势”，通常带来全局或分类型判定加成。

build_player() 把选择合并进世界观模板，返回新模板（不修改原模板）。
"""
import copy


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


def summarize_build(talent_names_list, physique_name, golden_finger_name):
    """把主角构建结果渲染成一段可读摘要。"""
    lines = [
        "天赋：" + ("、".join(talent_names_list) if talent_names_list else "无"),
        "体质：" + (physique_name or "无"),
        "金手指：" + (golden_finger_name or "无"),
    ]
    return "\n".join(lines)


def build_player(template, talent_names_list=None, physique_name=None, golden_finger_name=None):
    """把主角构建选择合并进世界观模板，返回新模板（原模板不被修改）。

    - talent_names_list: 自选天赋名列表（0~MAX_TALENTS 个），覆盖模板自带的天赋。
    - physique_name: 体质名（对应 PHYSIQUES）。
    - golden_finger_name: 金手指名（对应 GOLDEN_FINGERS；None 表示不带）。
    """
    t = copy.deepcopy(template)
    p = t.setdefault("player", {})
    if not isinstance(p, dict):
        p = {}
        t["player"] = p

    # 自选天赋（覆盖模板自带天赋）
    talents = []
    for name in (talent_names_list or []):
        entry = _find(TALENTS, name)
        talents.append({"name": name, "description": (entry or {}).get("description", "")})
    p["talents"] = talents

    # 体质
    p.pop("physique", None)
    physique = _find(PHYSIQUES, physique_name)
    if physique:
        p["physique"] = {"name": physique["name"], "description": physique["description"]}
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
    gf = _find(GOLDEN_FINGERS, golden_finger_name)
    if gf and gf.get("name"):
        p["golden_finger"] = {"name": gf["name"], "description": gf["description"]}

    return t
