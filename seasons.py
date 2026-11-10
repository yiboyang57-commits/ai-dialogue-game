"""岁月模式：把“一段时间的安排”交给代码推进。

- 场景模式：一个行动一回合（原有玩法），时间按片刻、时辰走。
- 岁月模式：玩家给出一段安排（如“在洞府修炼一月”“闭关一年冲击筑基”），
  代码按旬（10 天）逐段掷随机事件：
    · 小插曲：不打断，交给主持人在概括里一笔带过；
    · 打断事件：需要玩家做决定（来访、求助、寻衅、机缘、变故……），时间停在那一旬，
      剩余天数记为“被打断的安排”，玩家处理完可以继续。
- 日历天数、年龄、休养回血都由代码结算；主持人只负责把这段日子写出来，并补充幕后进展。

所有概率与事件都在本文件的表里，可直接增删调整。
"""
import math
import random

SLICE_DAYS = 10            # 一旬
MAX_BLOCK_DAYS = 3600      # 单次安排最长十年
DURATIONS = {"一旬": 10, "一月": 30, "一季": 90, "半年": 180, "一年": 360}

# 每一旬触发事件的概率。闭关时外界打扰少得多。
# 例：普通状态安排一个月（3 旬）被打断的概率约 22%，一年约 95%（平均四个月左右会遇到一件事）；
#     闭关一年被打断的概率约 67%。
INTERRUPT_RATE = {"normal": 0.08, "seclusion": 0.03}
FLAVOR_RATE = {"normal": 0.25, "seclusion": 0.10}
# 每一旬恢复的生命比例（按生命上限计）
HEAL_PER_SLICE = {"normal": 0.15, "seclusion": 0.25}
MAX_FLAVORS_IN_BRIEF = 4

# 小插曲：(描述, 是否涉及一位已知角色)
FLAVOR_EVENTS = [
    ("听到一则与附近势力或人物有关的传闻", False),
    ("日常中小有所得（少量钱财、材料或消息）", False),
    ("修行或技艺上有一点细微的感悟", False),
    ("相识之人托人捎来口信", True),
    ("附近发生了一件与主角无关、却能看出局势变化的小事", False),
    ("天象或灵气出现异动", False),
]

# 打断事件：(类别, 描述, 普通权重, 闭关权重, 是否涉及一位已知角色)
INTERRUPT_EVENTS = [
    ("机缘", "发现一个值得追查的机缘线索", 3.0, 3.0, False),
    ("委托", "有人带着交易或委托找上门", 3.0, 1.0, False),
    ("来访", "相识之人找上门来", 3.0, 1.5, True),
    ("求助", "有人遇险前来求助", 2.0, 1.0, False),
    ("冲突", "有人寻衅，或旧怨找上门", 2.0, 1.0, False),
    ("变故", "所在之地发生变故（天灾、兽潮、搜查、战乱等）", 1.5, 1.5, False),
    ("异状", "修行或身体出现异状，需要立即处理", 1.0, 2.5, False),
]


def clamp_days(days, default=30):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = default
    return max(1, min(MAX_BLOCK_DAYS, days))


def span_text(days):
    """天数转成口语时长：8 天 / 约一旬 / 约 3 个月 / 约 1 年 2 个月。"""
    days = int(days)
    if days < SLICE_DAYS:
        return f"{days} 天"
    if days < 30:
        return f"约 {days} 天"
    if days < 360:
        return f"约 {round(days / 30)} 个月"
    years, rest = divmod(days, 360)
    months = round(rest / 30)
    return f"约 {years} 年" + (f" {months} 个月" if months else "")


def _pick_person(known, rng):
    known = [k for k in (known or []) if isinstance(k, str) and k]
    return rng.choice(known) if known else None


def roll_block(days, seclusion=False, known=None, rng=random):
    """逐旬掷骰，返回本次安排的结算结果（不含任何叙事）。

    返回 {"planned", "elapsed", "remaining", "seclusion", "flavors": [...], "interrupt": {...}|None}
    """
    days = clamp_days(days)
    mode = "seclusion" if seclusion else "normal"
    slices = math.ceil(days / SLICE_DAYS)
    flavors, interrupt, elapsed = [], None, days
    for i in range(1, slices + 1):
        r = rng.random()
        if r < INTERRUPT_RATE[mode]:
            weights = [e[3] if seclusion else e[2] for e in INTERRUPT_EVENTS]
            kind, text, _, _, person = rng.choices(INTERRUPT_EVENTS, weights=weights)[0]
            interrupt = {"slice": i, "kind": kind, "event": text,
                         "who": _pick_person(known, rng) if person else None}
            elapsed = min(days, i * SLICE_DAYS)
            break
        if r < INTERRUPT_RATE[mode] + FLAVOR_RATE[mode]:
            text, person = rng.choice(FLAVOR_EVENTS)
            flavors.append({"slice": i, "event": text, "who": _pick_person(known, rng) if person else None})
    return {
        "planned": days,
        "elapsed": elapsed,
        "remaining": days - elapsed,
        "seclusion": bool(seclusion),
        "flavors": flavors,
        "interrupt": interrupt,
    }


def heal_amount(max_hp, elapsed, seclusion=False):
    """休养回血：按经过的旬数和生命上限计算。"""
    slices = math.ceil(max(0, elapsed) / SLICE_DAYS)
    rate = HEAL_PER_SLICE["seclusion" if seclusion else "normal"]
    return int(round(max_hp * rate * slices))


def plan_text(plan, days, seclusion=False):
    """界面发起安排时，写进对话记录里的玩家输入。"""
    return f"【安排一段时间】{plan}" + ("（闭关）" if seclusion else "") + f"，为期{span_text(days)}"


def _who(ev):
    return f"（涉及「{ev['who']}」）" if ev.get("who") else ("（一位新面孔）" if ev.get("who") is None and "相识" in ev["event"] else "")


def block_brief(plan, result):
    """给主持人的结算简报：经过多久、有哪些插曲、是否被打断，以及写作要求。"""
    lines = ["【系统已结算的时间安排（结果由系统决定，必须遵守）】"]
    lines.append(f"安排：{plan}" + ("（闭关）" if result["seclusion"] else "") + f"；计划 {span_text(result['planned'])}。")
    intr = result["interrupt"]
    if intr:
        lines.append(f"实际经过 {span_text(result['elapsed'])} 后被打断"
                     + (f"，剩余 {span_text(result['remaining'])}，玩家之后可以选择继续。" if result["remaining"] else "。"))
    else:
        lines.append(f"已完整经过 {span_text(result['elapsed'])}。")
    lines.append("这段时间主角得到了休养，生命已由系统结算，不要修改 hp。")
    if result["flavors"]:
        lines.append("期间的小插曲（按时间先后，平淡的日子一笔带过即可）：")
        for ev in result["flavors"][:MAX_FLAVORS_IN_BRIEF]:
            lines.append(f"- 第 {ev['slice']} 旬前后：{ev['event']}{_who(ev)}")
    else:
        lines.append("期间风平浪静，没有值得一提的插曲。")
    if intr:
        lines.append(f"打断事件：第 {intr['slice']} 旬前后，{intr['event']}{_who(intr)}。"
                     "请先概括此前的日子，再把场景停在这件事刚发生、需要玩家回应的那一刻，不要替玩家做决定。")
    else:
        lines.append("请概括这段时间，在安排结束、玩家需要决定下一步的时刻停下。")
    lines.append(
        "写作要求：\n"
        "- 用几段话写出时间流逝的感觉与修炼/经营/生活的进展，长度与经过的时间相称，不写逐日流水账。\n"
        "- 进展要与经过的时间相称：可以用 attribute_events、player_power（确有突破时）、inventory_add 记录合理的收获与成长，不能凭空暴涨。\n"
        "- 在 offscreen 里写 1~3 条这段时间里玩家并不知道的幕后进展：已知角色或势力各自做了什么、局势如何变化。\n"
        "- time 更新为这段时间结束（或被打断）时的游戏内时间。\n"
        "- 不要再声明 combat、judgment 或 time_block；options 给出此刻的 2~4 个选项。"
    )
    return "\n".join(lines)
