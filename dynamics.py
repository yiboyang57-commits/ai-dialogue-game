"""动态系数：好感度等系数随事件/时间动态增减。

- 好感度(affinity)由三部分组成：
  1) 基础值（初始/事件累计，存 characters[name].affinity）
  2) 相识时长增长（每 TIME_BONUS_PER_TURNS 回合 +1，封顶 TIME_BONUS_CAP）
  3) 关系事件增减（RELATION_EVENTS：救命之恩/并肩作战/赠礼/冒犯/背弃…）
- 其它系数（声望/魅力/福缘/体力…）通过 attribute_events 按事件增减（ATTRIBUTE_EVENTS）。

本文件只放“数据表 + 纯函数”，具体写入逻辑在 world_state.apply_update。
"""

RELATION_EVENTS = {
    "救命之恩": 30,
    "雪中送炭": 20,
    "并肩作战": 15,
    "仗义执言": 10,
    "共同经历": 8,
    "赠礼": 6,
    "相谈甚欢": 5,
    "久别重逢": 5,
    "冒犯": -10,
    "误会": -8,
    "欺骗": -25,
    "背弃": -40,
}

# 属性事件：事件标签 -> {属性名: 增减量}（可自行增删；hp 尽量由战斗系统结算）
ATTRIBUTE_EVENTS = {
    "声望大涨": {"声望": 15},
    "声名狼藉": {"声望": -15},
    "魅力大增": {"魅力": 10},
    "福缘提升": {"福缘": 8},
    "精疲力竭": {"体力": -20},
    "休整": {"体力": 10},
    "修为精进": {"修为": 10},
}

TIME_BONUS_PER_TURNS = 5   # 每相识 5 回合 +1 好感
TIME_BONUS_CAP = 15        # 相识时长带来好感的上限


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def time_bonus(char, current_turn):
    """相识时长带来的好感加成。"""
    met = char.get("met_turn")
    if not isinstance(met, int):
        return 0
    turns = max(0, current_turn - met)
    return min(TIME_BONUS_CAP, turns // TIME_BONUS_PER_TURNS)


def effective_affinity(char, current_turn):
    """有效好感度 = 基础好感 + 相识时长加成，钳制在 0~100。"""
    base = char.get("affinity", 0)
    if not isinstance(base, (int, float)):
        base = 0
    return clamp(int(base) + time_bonus(char, current_turn), 0, 100)
