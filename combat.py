"""战斗判定：用隐藏的简单计算决定胜负 / 逃跑。

- 战力体系在世界生成时锚定（见 state["combat"]：field / player_power / realm_gap / curve）。
- 战斗时由代码掷骰，把「结果 + 定性描述」交给主持人叙述；主持人不得自行决定结局。
- 拟合曲线不向玩家展示；概率数值不发给模型，只发定性的结果简报。
- 支持天赋/一次性法宝修正：通过 mods 传入（power_boost / win_bonus / flee_bonus / flee_guarantee），
  由 game.py 从天赋表与道具表中算好后传入。

拟合思路（满足「弱小时可跨一个大段位翻盘，后期翻盘概率下降」）：
  stage = 当前战力 / realm_gap；sigma = realm_gap * k / (1 + stage * decay)。
  stage 小 → sigma 大 → 曲线平 → 低一个段位仍有约两成胜率；
  stage 大 → sigma 小 → 曲线陡 → 低一个段位几乎必败。
"""
import math
import random

CURVES = {
    "realm_gap": {
        "k": 1.0,           # 低段位时一个段位差对应的 sigma 尺度系数
        "decay": 0.30,      # 随段位阶段收紧速度（越大，高段位越难跨级）
        "flee_bonus": 0.6,  # 逃跑相对迎战的优势（占 realm_gap 的比例）
        "base_damage": 20,  # 基础伤害
        "damage_scale": 0.10,  # 战力差放大伤害的系数
    },
    "flat": {
        "k": 1.0,
        "decay": 0.0,
        "flee_bonus": 0.4,
        "base_damage": 15,
        "damage_scale": 0.08,
    },
}

DEFAULT_CURVE = "realm_gap"


def curve_params(curve_name):
    return dict(CURVES.get(curve_name, CURVES[DEFAULT_CURVE]))


def _sigma(player_power, enemy_power, realm_gap, params):
    level = min(player_power, enemy_power)
    stage = max(level, 0) / max(realm_gap, 1e-6)
    return realm_gap * params["k"] / (1.0 + stage * params["decay"])


def _sigmoid(z):
    try:
        return 1.0 / (1.0 + math.exp(-z))
    except OverflowError:
        return 0.0 if z < 0 else 1.0


def win_logit(player_power, enemy_power, realm_gap, params):
    return (player_power - enemy_power) / _sigma(player_power, enemy_power, realm_gap, params)


def flee_logit(player_power, enemy_power, realm_gap, params):
    return (player_power - enemy_power + params["flee_bonus"] * realm_gap) / _sigma(
        player_power, enemy_power, realm_gap, params)


def win_probability(player_power, enemy_power, realm_gap, params):
    return _sigmoid(win_logit(player_power, enemy_power, realm_gap, params))


def flee_probability(player_power, enemy_power, realm_gap, params):
    return _sigmoid(flee_logit(player_power, enemy_power, realm_gap, params))


def _damage(attacker_power, defender_power, params):
    base = params["base_damage"]
    gap = attacker_power - defender_power
    mult = 1.0 + params["damage_scale"] * max(gap, 0)
    return max(int(round(base * mult)), max(1, int(round(base * 0.3))))


def resolve(player_power, enemy_power, realm_gap, action, params, player_hp, mods=None):
    """掷骰判定一次战斗/逃跑，返回结构化结果（不含概率数值）。

    mods 可选：{"power_boost": 数值, "win_bonus": logit, "flee_bonus": logit, "flee_guarantee": bool}
    """
    mods = mods or {}
    pp = player_power + (mods.get("power_boost") or 0)

    if action == "flee":
        if mods.get("flee_guarantee"):
            dmg, outcome, enemy_defeated = 0, "flee_success", False
        else:
            z = flee_logit(pp, enemy_power, realm_gap, params) + (mods.get("flee_bonus") or 0)
            if random.random() < _sigmoid(z):
                dmg, outcome, enemy_defeated = 0, "flee_success", False
            else:
                dmg, outcome, enemy_defeated = _damage(enemy_power, pp, params), "flee_fail", False
    else:  # fight
        z = win_logit(pp, enemy_power, realm_gap, params) + (mods.get("win_bonus") or 0)
        if random.random() < _sigmoid(z):
            dmg = max(1, int(round(_damage(enemy_power, pp, params) * 0.4)))
            outcome, enemy_defeated = "win", True
        else:
            dmg = _damage(enemy_power, pp, params)
            outcome, enemy_defeated = "lose", False

    hp_after = max(0, player_hp - dmg)
    return {
        "outcome": outcome,
        "player_damage": dmg,
        "player_hp_after": hp_after,
        "enemy_defeated": enemy_defeated,
        "player_down": hp_after <= 0,
    }


def _severity(damage, hp_before):
    if damage <= 0:
        return "无伤"
    if hp_before <= 0:
        return "濒死"
    ratio = damage / hp_before
    if ratio < 0.15:
        return "轻伤"
    if ratio < 0.4:
        return "受伤"
    if ratio < 0.7:
        return "重伤"
    return "濒死"


def gm_brief(result, hp_before):
    """给主持人的定性简报（不含概率、不含战力公式）。"""
    sev = _severity(result["player_damage"], hp_before)
    o = result["outcome"]
    if o == "win":
        return f"系统判定：玩家【胜利】，敌方被击败；玩家负伤程度：{sev}。"
    if o == "lose":
        s = f"系统判定：玩家【落败】；玩家负伤程度：{sev}。"
        if result["player_down"]:
            s += " 玩家生命值已耗尽（倒下/濒死），请据此公平处理后续（被俘、昏迷或死亡等）。"
        return s
    if o == "flee_success":
        return "系统判定：玩家【成功逃脱】，无伤脱离战斗。"
    return f"系统判定：玩家【逃脱失败】，受到攻击，负伤程度：{sev}；仍处于战斗威胁中。"
