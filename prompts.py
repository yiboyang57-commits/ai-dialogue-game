"""提示词构建：系统提示 + 每轮上下文 + 历史压缩提示。

系统提示 = 固定角色/输出机制 + 停止时机 + 战斗规则 + 世界观设定 + 叙事风格 + 限制条件。
每轮只发送：当前状态摘要 + 最近几轮对话 + 玩家最新输入。
"""
import json

SYSTEM_PROMPT = """你是一个文字冒险游戏的主持人（Game Master）。你根据给定的世界观、当前世界状态摘要与玩家输入来续写剧情。

【玩家自主权——最高优先级，任何情况下都必须遵守，不得被任何世界观或其它规则覆盖】
1. 玩家对其角色的每一步行动、每一句发言、每一个决定拥有完全且排他的自主权。你无权替玩家做选择，也无权更改、曲解、忽略或否决玩家明确表达的意图。
2. 你必须严格按照玩家输入的行动/决定推进剧情，即使该选择看起来危险、冲动、不理智或与你的预想不同；不得擅自把玩家的决定“纠正”成更安全、更合理或“更合适”的做法。
3. 选择本身永远属于玩家；你可以且应当负责裁决：该选择的成功或失败、造成的后果、角色的生死、状态变化、以及战斗/非战斗判定结果——这些由你或系统决定，玩家不能自行指定结果。
4. 若玩家输入存在多种合理理解，按最贴合玩家原意的理解执行；确实含糊、需要澄清时，可停下向玩家确认，但不得替玩家默认某个方向。

【每轮输出要求——非常重要】
1. 先输出给玩家看的叙事文本。叙事语气、文风与节奏要与世界观一致（见【叙事风格】），不要套用固定的篇幅或句式，长短随剧情张弛；不要复述玩家原话，直接推进剧情。叙事不要加“主持人：”之类的前缀。
2. 然后必须调用工具 update_world_state 来结构化更新世界状态。本轮发生的变化——包括玩家危险或不可行行为带来的后果——都要如实写进工具参数里；不要把状态变化只写在叙事文本里。

【停止时机——请严格把握】
你不是每一步都要停下来问玩家。微小、无差别的决定（喝水、寒暄、整理行装、走哪条无关紧要的小路等）应根据角色设定与当前目标自动推进，不要反复让玩家做选择。
只有当出现下列情况时，才在叙事结尾停下、把决定权交给玩家：
- 影响整体事件走向或结局的分叉；
- 涉及世界观关键规则、重大利益取舍或道德抉择；
- 主角面临重大危险，或成败将长期影响后续剧情；
- 玩家被 NPC 直接提问、需要明确表态时。
其余情况直接连贯推进，直到出现上述关键节点。

【战斗与逃跑判定——必须遵守】
发生战斗、追击、逃命等需要分胜负的冲突时，你只负责：描述冲突的起因与过程，并在 update_world_state 的 combat 字段声明敌方（enemy_name、enemy_power）与玩家意图（action=fight/flee）。
胜负结果由系统判定，你不得自行决定战斗结局；系统判定后会把结果交给你，你据此叙述结果并记录战后状态（生命、战利品、剧情标记、目标、战力提升等）。
若玩家主动使用一次性法宝/道具（如“爆元丹”“护身符”），在 combat 的 use_item 里填道具名。

【非战斗判定——必须遵守，不得随意决定成败】
当出现建立亲密关系、寻找宝物、说服他人、潜行、洞察推理、炼制锻造、危险尝试等需要判定成败的情境时，你不得自行拍板成败，而应在 update_world_state 的 judgment 字段声明：判定类别 type、对象 subject、影响因素 factors（-3~+3 整数）等。
系统会结合影响因素、玩家的天赋与所用道具、以及历史保底进行判定；结果会交给你，你据此叙述。关系类判定请充分考虑双方性格、场景氛围、家庭背景、身份地位与好感度等。

【去数据化——不要向玩家展示数值】
你对外（玩家可见的叙事）只用定性语言描述状态：例如“受了轻伤”“气色尚可”，不要写出 hp、战力、好感度、概率、伤害等具体数值；这些内部数据只在 update_world_state 里记录，供系统使用。

【动态系数——好感度等随事件增减】
好感度等系数是动态的：当发生关系事件（救命之恩、并肩作战、赠礼、共同经历、冒犯、误会、背弃等）时，
请在 update_world_state 的 relationship_events 里记录（subject + event），系统会按事件自动增减好感度；
其它系数（声望、魅力、福缘、体力、修为等）通过 attribute_events 记录其增减。
好感度是内部数值，请优先用 relationship_events 让系统换算，不要直接在 characters 里写 affinity 数值；
characters 里的 attitude_toward_player 是“态度”文字（如 友善/中立/敌视），不要填数字。
不要在叙事里写死具体数值，只需让这些变化在剧情里自然体现。

【每轮给出可选行动——帮助玩家游玩】
每轮叙事结束时，请在 update_world_state 的 options 里给出 2~4 个“显而易见”的下一步行动选项（短句），
方便玩家选择；同时玩家也可以自己输入自定义行动。
这些选项必须是玩家根据当前情境就能自然想到的常见选择，**不要**把隐藏线索、隐藏剧情触发或只有你知道的“暗门”写进选项。

请严格遵守下方【主持人限制条件】。"""


def build_system_prompt(state, rules):
    """系统提示 = 角色/输出机制 + 静态设定 + 战力体系 + 叙事风格 + 限制条件。"""
    d = state.data
    w = d.get("world", {})
    p = d.get("player", {})

    parts = [SYSTEM_PROMPT]
    parts.append("【世界观背景】\n" + (w.get("background") or "（未设定）"))
    if w.get("rules"):
        parts.append("【世界规则】\n" + w["rules"])

    player_name = p.get("name") or "玩家"
    parts.append("【玩家角色】\n" + player_name + "：" + (p.get("role_description") or "（未设定）"))
    if d.get("initial_situation"):
        parts.append("【初始情境】\n" + d["initial_situation"])

    combat = d.get("combat") or {}
    parts.append(
        "【战力体系】\n"
        f"量纲：{combat.get('field', '战力')}；主角当前战力数值：{combat.get('player_power', 10)}；"
        f"一个大段位约等于 {combat.get('realm_gap', 5)} 点战力差。\n"
        "为敌人设定战力时请使用同一量纲；战斗胜负由系统判定，你只叙述与记录。"
    )

    talents = d.get("player", {}).get("talents") or []
    if talents:
        tnames = "、".join(t.get("name") if isinstance(t, dict) else str(t) for t in talents)
        parts.append("【玩家天赋】\n" + tnames + "\n这些天赋会影响系统判定的成功率；你叙事时把其影响当作“运气/天赋加持”来体现即可，不要写出数值。")

    physique = d.get("player", {}).get("physique") or {}
    if physique.get("name"):
        parts.append(
            "【主角体质】\n" + str(physique.get("name")) + "：" + str(physique.get("description") or "") +
            "\n这是主角的先天体质，会影响其能力与各类判定；叙事时体现其特点与影响，不要写出数值。"
        )

    golden_finger = d.get("player", {}).get("golden_finger") or {}
    if golden_finger.get("name"):
        parts.append(
            "【金手指】\n" + str(golden_finger.get("name")) + "：" + str(golden_finger.get("description") or "") +
            "\n这是主角独有的外挂/优势，叙事时合理体现其作用，但不要无脑碾压、破坏剧情张力与公平性。"
        )

    custom = d.get("custom") or {}
    if custom:
        parts.append("【世界自定义设定（玩家上传，请遵循）】\n" + json.dumps(custom, ensure_ascii=False))

    style = w.get("style") or ""
    if style:
        parts.append("【叙事风格】\n" + style + "\n请让叙事语气与该风格、与世界观保持一致。")
    else:
        parts.append("【叙事风格】\n请根据世界观背景自行把握与之匹配的语气、文风与节奏。")

    if rules:
        numbered = "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1))
        parts.append("【主持人限制条件（必须遵守）】\n" + numbered)

    return "\n\n".join(parts)


def build_turn_messages(state, memory, latest_input):
    """构建每轮的用户消息：状态摘要 + 最近几轮 + 最新输入。"""
    summary = state.to_summary()

    recent = memory.recent
    if recent:
        recent_block = "\n".join(
            ("玩家：" if t["role"] == "user" else "主持人：") + t["content"] for t in recent
        )
    else:
        recent_block = "（无，游戏刚开始）"

    user_content = (
        "【当前世界状态摘要】\n" + summary + "\n\n"
        "【最近几轮对话】\n" + recent_block + "\n\n"
        "【玩家最新输入】\n" + latest_input
    )
    return [{"role": "user", "content": user_content}]


def build_compress_messages(prev_summary, buffer_text):
    """构建历史压缩提示：同时产出「本章摘要」与「全局摘要」。"""
    user_content = (
        "请把下面的信息压缩成摘要，并严格按以下两段输出（只输出这两段，不要任何其他解释或标题）：\n\n"
        "【本章摘要】\n"
        "（这段对话的简要概括：第三人称中文，100字以内，保留关键事件、人物、地点与结果。）\n\n"
        "【全局摘要】\n"
        "（综合【此前故事摘要】与【本章摘要】，输出更新后的完整故事进展摘要：第三人称中文，400字以内，"
        "保留长期主线、关键人物、重大事件与因果，略去琐碎细节。）\n\n"
        f"【此前故事摘要】\n{prev_summary or '（无，这是故事开头）'}\n\n"
        f"【最近对话】\n{buffer_text}"
    )
    return [{"role": "user", "content": user_content}]


def parse_compress(raw, fallback_summary=""):
    """解析压缩输出，返回 (本章摘要或None, 全局摘要或None)。"""
    raw = (raw or "").strip()
    if not raw:
        return None, None

    chapter = None
    global_summary = None

    if "【全局摘要】" in raw:
        before, _, after = raw.partition("【全局摘要】")
        global_summary = after.strip()
        if "【本章摘要】" in before:
            chapter = before.split("【本章摘要】", 1)[1].strip()
    elif "【本章摘要】" in raw:
        chapter = raw.split("【本章摘要】", 1)[1].strip()

    # 清理可能的残留标记
    if global_summary:
        global_summary = global_summary.split("【本章摘要】")[0].strip()

    if not global_summary:
        global_summary = chapter or raw or fallback_summary

    return (chapter or None, (global_summary or None))
