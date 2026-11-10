"""主持人限制条件：读取 / 生成 gm_rules.json。

限制条件独立于世界观，是用户对主持人的全局约束。
首次运行会生成带默认值的 gm_rules.json，用户可直接编辑该文件增删规则。
"""
import json
import os

from config import ROOT_DIR

RULES_PATH = os.path.join(ROOT_DIR, "gm_rules.json")

# 本作面向成年玩家（历史上曾有一条 PG-13 规则，已按产品定位移除）。
# 尺度的正面说明与底线写在 prompts.SYSTEM_PROMPT 的【内容尺度——成人向】里；
# 如需额外收紧，在此数组与 gm_rules.json 的 rules 里同步添加即可。
DEFAULT_RULES = [
    "玩家的每一步行动与决定拥有完全自主权：主持人不得替玩家做选择、不得更改/曲解/忽略/否决玩家的选择；有失败可能的行动由系统判定成败与生死，主持人据此叙述后果，但选择本身永远属于玩家。",
    "不为玩家做决定，不替玩家发言或行动；遇到关键抉择时停下等待玩家输入。",
    "严格遵守世界观设定与状态摘要中的事实，不凭空引入与设定矛盾的要素。",
    "保持因果合理：不机械降神、不强行反转、不无端洗白或抹黑角色。",
    "不无端大幅跳跃时间；重大的时间或场景跳跃需由剧情合理推动。",
    "失败、受伤或死亡要有前因后果，公平可预期，不突然无理由判死。",
    "每轮只处理玩家本次行动及直接后果，完成就停；不得擅自继续赶路、接任务或跳过大量时间。",
    "玩家没有主角保护。已有条件下的致命后果必须结算，不能凭空用救援、觉醒、昏迷或复活回避死亡。",
]


def load_rules():
    """读取 gm_rules.json；文件缺失或损坏时回退到默认规则。"""
    if os.path.exists(RULES_PATH):
        try:
            with open(RULES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            rules = data.get("rules")
            if isinstance(rules, list):
                cleaned = [str(r).strip() for r in rules if str(r).strip()]
                if cleaned:
                    return cleaned
        except (json.JSONDecodeError, OSError):
            pass
    return list(DEFAULT_RULES)


def ensure_rules_file():
    """若规则文件不存在，写入默认规则，方便用户发现并编辑。"""
    if not os.path.exists(RULES_PATH):
        save_rules(DEFAULT_RULES)


def save_rules(rules):
    os.makedirs(os.path.dirname(RULES_PATH), exist_ok=True)
    with open(RULES_PATH, "w", encoding="utf-8") as f:
        json.dump({"rules": rules}, f, ensure_ascii=False, indent=2)
