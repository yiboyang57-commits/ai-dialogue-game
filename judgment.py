"""通用概率判定引擎：拟合影响因素 + 天赋/道具系数 + 大保底。

设计目标：
- 亲密关系、寻宝、说服、潜行、洞察、炼制、危险行为等成败，不由主持人（LLM）随意决定，
  而是由代码把「影响因素」拟合成一个概率，掷骰判定（对玩家隐藏）。
- 判定「不写死」：所有权重/系数都在本文件的表中，可增删修改。
- 天赋（如“天命之子”）作为对数几率(logit)系数叠加，大幅影响成功率。
- 一次性法宝/特殊道具也可按名字叠加系数，并支持消耗。
- 大保底：同一类判定连续失败达到阈值后，强制成功。

概率模型：
  logit = base + Σ(因素权重 × 因素值) + Σ(天赋系数) + 道具系数
  p = sigmoid(logit)，并受保底钳制。
因素值由主持人声明为 -3(很不利) ~ +3(很有利) 的整数。
"""
import math
import random

# ---- 判定类型：基础 logit、因素权重、保底阈值 ----
JUDGMENT_TYPES = {
    "relationship": {
        "base": -3.0,   # 刚见面就建立亲密关系，本身极低
        "factors": {
            "双方性格契合": 0.8,
            "场景氛围契合": 0.5,
            "家庭背景契合": 0.4,
            "双方身份地位契合": 0.3,
            "当前好感度": 1.2,
            "相处时长/见面次数": 0.6,
        },
        "pity": 12,
    },
    "treasure": {
        "base": -1.5,
        "factors": {
            "福缘/运气": 1.0,
            "场景线索契合": 0.6,
            "投入时间/线索": 0.5,
            "洞察/风水": 0.4,
        },
        "pity": 8,
    },
    "persuasion": {
        "base": -0.5,
        "factors": {
            "口才/魅力": 1.0,
            "对方好感度": 0.8,
            "筹码/利益": 0.6,
            "形势/紧迫度": 0.4,
        },
        "pity": 6,
    },
    "insight": {
        "base": 0.0,
        "factors": {"智力/洞察": 1.0, "线索完整度": 0.7, "专注/冷静": 0.4},
        "pity": 6,
    },
    "sneak": {
        "base": -0.3,
        "factors": {"身手/敏捷": 0.9, "环境掩护": 0.5, "对方警觉度": -0.6},
        "pity": 6,
    },
    "craft": {
        "base": -0.8,
        "factors": {"技艺/熟练度": 1.0, "材料品质": 0.6, "环境/火候": 0.4},
        "pity": 8,
    },
    "action": {
        "base": 0.0,
        "factors": {"能力匹配": 1.0, "准备充分度": 0.5, "环境有利度": 0.4, "风险程度": -0.7},
        "pity": 8,
    },
}

DEFAULT_JUDGMENT = "action"

# ---- 天赋 → 各判定类别/全局 的 logit 系数 ----
# “天命之子”等天赋在这里挂系数；global 作用于所有判定（含战斗）。
TALENT_MODIFIERS = {
    "天命之子": {"global": 1.2, "treasure": 0.8, "relationship": 0.5},
    "福星高照": {"treasure": 1.5, "global": 0.3},
    "魅惑天成": {"relationship": 1.5, "persuasion": 0.8},
    "巧舌如簧": {"persuasion": 1.5},
    "神机妙算": {"insight": 1.5, "treasure": 0.5},
    "身手敏捷": {"sneak": 1.2},
    "百战之躯": {"combat": 1.0, "flee": 0.6},
    "炼器天才": {"craft": 1.5},
    "气运加身": {"global": 0.8},
    "倒霉体质": {"global": -0.6},
    # ---- 体质（主角构建自选，见 character_builder.PHYSIQUES）----
    "均衡之躯": {},
    "先天道体": {"global": 0.5, "insight": 0.4},
    "纯阳之体": {"combat": 0.6},
    "玄阴之体": {"insight": 0.8, "sneak": 0.6},
    "霸绝武体": {"combat": 1.0},
    "万毒不侵": {"craft": 0.5, "action": 0.4},
    # ---- 金手指（主角构建自选，见 character_builder.GOLDEN_FINGERS）----
    "随身系统": {"global": 0.6, "insight": 0.5},
    "天机推演": {"insight": 1.2, "global": 0.3},
    "存档读档": {"global": 1.0},
    "天命气运": {"global": 1.0, "treasure": 1.0},
    "点石成金": {"craft": 1.0, "treasure": 0.5},
}

# ---- 道具（法宝/特殊道具）→ 系数与是否一次性 ----
# combat_power: 本场战力加成；combat: 战斗胜率 logit 加成；flee_guarantee: 逃跑必定成功；
# 其余键为判定类别名，表示对该类判定的 logit 加成。
ITEM_MODIFIERS = {
    "寻宝罗盘": {"treasure": 1.2, "consumable": False},
    "护身符": {"flee_guarantee": True, "consumable": True},
    "爆元丹": {"combat_power": 2.0, "combat": 0.5, "consumable": True},
    "魅力香囊": {"relationship": 1.0, "persuasion": 0.5, "consumable": True},
    "锦囊妙计": {"insight": 1.5, "consumable": True},
    "破境丹": {"combat_power": 3.0, "consumable": True},
}


def _sigmoid(z):
    try:
        return 1.0 / (1.0 + math.exp(-z))
    except OverflowError:
        return 0.0 if z < 0 else 1.0


def talent_bonus(category, talents):
    """累加天赋对某类判定（category）的 logit 系数。talents 为天赋名列表。"""
    total = 0.0
    for t in (talents or []):
        m = TALENT_MODIFIERS.get(t) or {}
        total += m.get("global", 0.0) + m.get(category, 0.0)
    return total


def item_mods(name):
    return ITEM_MODIFIERS.get(name) or {}


def compute_judgment_probability(jtype, factors, talents, item_bonus_logit, pity_failures):
    """拟合概率（未掷骰），pity_failures 达到保底阈值时返回 1.0。"""
    spec = JUDGMENT_TYPES.get(jtype) or JUDGMENT_TYPES[DEFAULT_JUDGMENT]
    logit = spec["base"]
    fw = spec["factors"]
    for name, val in (factors or {}).items():
        w = fw.get(name, 0.3)  # 未知因素给温和权重，允许自定义因素
        try:
            logit += w * float(val)
        except (TypeError, ValueError):
            pass
    logit += talent_bonus(jtype, talents)
    logit += item_bonus_logit or 0.0

    if (pity_failures or 0) >= spec["pity"]:
        return 1.0  # 大保底
    return min(max(_sigmoid(logit), 0.001), 0.999)


def resolve_judgment(jtype, factors, talents, item_bonus_logit, pity):
    """掷骰判定，更新保底计数，返回 (成功?, 概率)。"""
    fails = (pity or {}).get("fails", 0)
    p = compute_judgment_probability(jtype, factors, talents, item_bonus_logit, fails)
    success = random.random() < p

    if success:
        pity["fails"] = 0
        pity["successes"] = pity.get("successes", 0) + 1
    else:
        pity["successes"] = 0
        pity["fails"] = fails + 1
    return success, p


def judgment_brief(jtype, success, subject):
    """给主持人的定性简报（不含概率数值）。"""
    s = subject or "该次尝试"
    if success:
        return f"系统判定：{s}——【成功】。请据此叙述成功的具体表现与结果。"
    return f"系统判定：{s}——【失败】。请据此叙述失败的表现与后果（保留后续再次尝试的空间）。"
