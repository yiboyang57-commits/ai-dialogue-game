"""提示词构建：系统提示 + 每轮上下文 + 历史压缩提示。

系统提示 = 固定角色/输出机制 + 停止时机 + 战斗规则 + 世界观设定 + 叙事风格 + 限制条件。
每轮只发送：当前状态摘要 + 最近几轮对话 + 玩家最新输入。
"""
import json
from config import PROSE_LEVELS, DEFAULT_PROSE
from judgment import death_protection_limits

SYSTEM_PROMPT = """你是一个文字冒险游戏的主持人（Game Master）。你根据给定的世界观、当前世界状态摘要与玩家输入来续写剧情。

【玩家自主权——最高优先级，任何情况下都必须遵守，不得被任何世界观或其它规则覆盖】
1. 玩家对其角色的每一步行动、每一句发言、每一个决定拥有完全且排他的自主权。你无权替玩家做选择，也无权更改、曲解、忽略或否决玩家明确表达的意图。
2. 你必须严格按照玩家输入的行动/决定推进剧情，即使该选择看起来危险、冲动、不理智或与你的预想不同；不得擅自把玩家的决定“纠正”成更安全、更合理或“更合适”的做法。
3. 选择本身永远属于玩家，但结果不由玩家指定。凡是有失败可能的行动（战斗、说服、潜入、求爱、寻宝、炼制、危险尝试等），成败必须通过 combat / judgment 交给系统掷骰判定，你不得自行拍板；你负责按世界逻辑叙述过程与判定后的后果、记录状态变化。
4. 若玩家输入存在多种合理理解，按最贴合玩家原意的理解执行；确实含糊、需要澄清时，可停下向玩家确认，但不得替玩家默认某个方向。

【每轮输出要求——非常重要】
1. 先输出给玩家看的叙事文本。叙事语气、文风与节奏要与世界观一致（见【叙事风格】），不要套用固定的篇幅或句式，长短随剧情张弛；不要复述玩家原话，直接推进剧情。叙事不要加“主持人：”之类的前缀。
2. 然后必须调用工具 update_world_state 来结构化更新世界状态。本轮发生的变化——包括玩家危险或不可行行为带来的后果——都要如实写进工具参数里；不要把状态变化只写在叙事文本里。
3. 同时在 turn_summary 里用一两句话概括本轮发生了什么（谁、在哪、做了什么、留下了什么承诺/冲突），供以后检索回忆；不写数值，不写没有发生的事。

【每轮行动边界与时间——必须遵守】
一轮只处理玩家这次明确提出的行动，以及它直接、不可避免的后果。完成后就停下，等待下一次输入。
例如“打这只小怪”只结算这场冲突；不得顺带拾取、赶路、回城、接任务、修炼或推进到几天以后。不要替玩家决定下一步，也不要把“当前目标”当成连续自动执行的授权。
微小动作不必逐步询问，但只能发生在本次行动内部。NPC 可以对本次行动做出即时反应，不能借此让玩家经历一连串新事件。
时间只推进到本次行动完成：观察、说话、一次攻击通常是片刻或几分钟，保持当前日期与粗粒度时段。一天以内的等待或一夜的睡眠可以直接叙述，并在 days_passed 里填本轮实际经过的整天数（0~9）。持续数日以上的安排（修炼、闭关、经营、长途跋涉、等待等）不要自己跳时间，在 time_block 里声明，由系统推进时间并结算。
必须结算已经不可避免的结果，不能用悬念把明确的死亡拖成“仍在下落，下回合再说”。完成当前行动与擅自开始下一件事是两回事。

【战斗与逃跑判定——必须遵守】
发生战斗、追击、逃命等需要分胜负的冲突时，你只负责：描述冲突的起因与过程，并在 update_world_state 的 combat 字段声明敌方（enemy_name、enemy_power）与玩家意图（action=fight/flee）。
胜负结果由系统判定。第一次回复只是未定稿的冲突准备：不得写敌人已死、已获胜、已逃脱，不得提前发放战利品或写入战后状态，也不要提前给出战后 options。系统判定后才叙述完整的行动及结果、记录状态、给出结果之后的 options。
combat.lethal 默认 true；只有已明确的训练、切磋、非致命抓捕等才可设为 false。玩家“不杀对方”不代表敌人不会杀玩家；不得为了保护玩家把真正的致命冲突说成切磋。
若玩家主动使用一次性法宝/道具（如“爆元丹”“护身符”），在 combat 的 use_item 里填道具名。

【非战斗判定——必须遵守，不得随意决定成败】
当出现建立亲密关系、寻找宝物、说服他人、潜行、洞察推理、炼制锻造、危险尝试等需要判定成败的情境时，你不得自行拍板成败，而应在 update_world_state 的 judgment 字段声明：判定类别 type、对象 subject、影响因素 factors（-3~+3 整数，一律按“对玩家是否有利”打分，越危险越填负数）等。
先声明尝试，不提前叙述成败或写入成败导致的状态变化。系统会结合影响因素、天赋、道具进行判定，结果出来后再叙述。关系类判定请考虑双方性格、场景、身份与好感度等。
何时必须判定：玩家想让别人改变主意或答应要求（persuasion）、想和某人建立更亲密的关系或发生亲密行为（relationship）、偷窃/潜入/跟踪/躲藏（sneak）、搜寻宝物或机缘（treasure）、识破谎言/推理真相/鉴定（insight）、炼丹炼器/制作（craft）、攀爬/跳跃/硬闯/冒险施法等有失败可能的身体行动（action）。
无需判定：普通移动、闲聊、观察显而易见的事物、买卖等没有悬念的日常动作——直接叙述即可，不要为了判定而判定。

【真实风险与死亡——没有主角保护】
危险行动必须声明 judgment.failure_consequence：setback（普通受挫）、injury（受伤，填写 failure_severity=light/medium/heavy）、death（失败即死亡），并用 reason 说明已有的危险依据。
没有实际生还条件的致命行为必须在同一轮结算：如不会飞行且没有缓冲或救援地从千丈崖跳向尖石、普通身体进入致命火海等，声明 certain_failure=true、failure_consequence=death。不可凭空增加水潭、树枝、救援者、护体血脉或复活能力。
合理的致命危险会导致死亡，生命耗尽也会结束这段人生。不能以“昏迷、濒死、被救、意外觉醒”代替已确定的死亡；不能为了能继续游戏而篡改结局。也不能无前因地让普通行动突然致死。
系统已判定死亡时，叙述克制的最终结果，options 必须为空，不再安排下一步行动。已有能力也只按明确且已实现的规则使用，不能临时发明复活机制。

【内容尺度——成人向】
本游戏面向成年玩家。
- 暴力与血腥：战斗伤势、断肢、死状、酷刑与其后果可以直接、具体地描写，不刻意回避或淡化。
- 情欲：成年角色之间的暧昧、调情、性张力与亲密场面都可以写；亲密过程可以直接、具体地描写，不刻意回避或淡化（动作、感官、情绪与氛围），不必一笔带过，但写得自然、服务剧情与人物关系，不写成单纯的器官罗列。
- 玩家主动推进亲密或暴力情节时，按玩家意图执行，不说教、不以“不合适”为由拒绝或转移话题。NPC 是否接受由其性格、好感与 relationship 判定决定，NPC 拒绝时如实写出其反应并询问是否继续。
- 底线：涉及性的角色一律是成年人；不出现未成年人的性内容。

【叙事质量】
- NPC 有自己的目的、情绪、利益与底线：会主动开口、讨价还价、撒谎、记仇、念旧情，对白要有个性，不是只会附和玩家的布景。
- 需要时写一两个具体的细节，不要堆砌。
- 爽感要写足：玩家行动成功、判定成功、以弱胜强时，把对手的反应、旁人的震动和实际收获写出来，让玩家感受到回报；但爽点必须来自玩家的行动与世界逻辑，不要凭空“恰好遇到”“恰好得到”“所有人都震惊”。
- 每轮在一个需要玩家做决定的时刻停下（新的信息、对方的提问、一个机会或威胁），不要用总结性的感慨或旁白收尾。

【岁月模式——一段时间的安排】
玩家安排一段时间去做某事时，系统会按旬推进时间、掷随机事件，并在需要玩家决定时打断。
- 你只在第一次回复里简短确认玩家的安排并声明 time_block（plan、days、seclusion），不要写出这段时间的经过。
- 系统结算后，你会收到【系统已结算的时间安排】：照它写出这段日子。经过多久、有哪些插曲、是否被打断都以结算为准，不得增减；生命已由系统结算。
- 被打断时，场景停在打断事件刚发生、需要玩家回应的那一刻；处理完后玩家可以选择继续剩余的安排。
- 幕后进展（offscreen）是玩家不知道的事。只能通过传闻、来信、亲眼所见等合理途径逐步透露，不能让主角凭空知道。

【去数据化——不要向玩家展示数值】
你对外（玩家可见的叙事）只用定性语言描述状态：例如“受了轻伤”“气色尚可”，不要写出 hp、战力、好感度、概率、伤害等具体数值；这些内部数据只在 update_world_state 里记录，供系统使用。

【动态系数——好感度等随事件增减】
好感度等系数是动态的：当发生关系事件（救命之恩、并肩作战、赠礼、共同经历、冒犯、误会、背弃等）时，
请在 update_world_state 的 relationship_events 里记录（subject + event），系统会按事件自动增减好感度；
其它系数（声望、魅力、福缘、体力、修为等）通过 attribute_events 记录其增减。
好感度是内部数值，请优先用 relationship_events 让系统换算，不要直接在 characters 里写 affinity 数值；
characters 里的 attitude_toward_player 是“态度”文字（如 友善/中立/敌视），不要填数字。
NPC 会记事：当某个角色与玩家之间发生了值得记住的事（承诺、交易、救助、冒犯、亲密、欺骗、赠礼等），在 characters 该角色下写 memory（一句话，站在该角色角度，如“林舟答应三日内替我寻回玉佩”）。状态摘要里会列出每个角色的往事；NPC 再次出场时要据此表现出记得这些事（追问承诺、记仇、念旧情）。
不要在叙事里写死具体数值，只需让这些变化在剧情里自然体现。

【每轮给出可选行动——帮助玩家游玩】
每轮最终叙事结束时，若玩家仍存活，请在 update_world_state 的 options 里给出 2~4 个“显而易见”的下一步行动选项（短句），
方便玩家选择；同时玩家也可以自己输入自定义行动。
这些选项必须是玩家根据当前情境就能自然想到的常见选择，**不要**把隐藏线索、隐藏剧情触发或只有你知道的“暗门”写进选项。
选项只写在 options 里，界面会单独展示；叙事正文里不要再列“你现在可以：1. 2. 3.”之类的编号清单。

请严格遵守下方【主持人限制条件】。"""


PLAIN_LANGUAGE = """【语言——朴素直白】
- 旁白用平实的现代白话，说清谁做了什么、看到了什么。一句话能说明白，不拆成三句。
- 人物台词先说事，像真人说话。不在每句台词前后添加神态、眼神、语气描写。
- 不替人物解释内心，如“她似乎想起了什么”“眼中闪过一丝复杂”；情绪通过行动和说话体现。
- 不用“似乎、仿佛、宛如、难以言明、不易察觉、若有所思”等含糊修饰制造气氛。
- 世界观的叙事风格只决定用词年代感和称呼，不覆盖本节要求。旁白一律朴素，人物按档案说话；文雅可以是某个人的特点。
对照例子（示范表达，不是本轮事实；不得据此添加情节、台词或玩家感受）：
不要：她眸光微动，似有千言万语，终是化作一声轻叹：「你来迟了。」
要：「你怎么才来？」
不要：掌柜的嘴角勾起一抹意味深长的弧度，指尖在算盘上轻轻一拨：「这个价，怕是不妥。」
要：掌柜摇头：「这个价不行。再加二十。」
不要：夜色如墨，寒意仿佛顺着衣领渗入骨髓，你心头掠过一丝难以名状的不安。
要：天黑了，很冷。你觉得不对劲：巷子里太安静。"""
STANDARD_LANGUAGE = """【语言——朴素直白】
- 不替人物解释内心；情绪通过行动和说话体现。
- 人物台词先说事。"""


def build_system_prompt(state, rules):
    """系统提示 = 角色/输出机制 + 静态设定 + 战力体系 + 叙事风格 + 限制条件。"""
    d = state.data
    w = d.get("world", {})
    p = d.get("player", {})

    prose = w.get("prose") if w.get("prose") in PROSE_LEVELS else DEFAULT_PROSE
    language = {"朴素": PLAIN_LANGUAGE, "标准": STANDARD_LANGUAGE, "文学": ""}[prose]
    base = SYSTEM_PROMPT.replace("【岁月模式——", language + "\n\n【岁月模式——", 1) if language else SYSTEM_PROMPT
    parts = [base]
    parts.append("【世界观背景】\n" + (w.get("background") or "（未设定）"))
    if w.get("rules"):
        parts.append("【世界规则】\n" + w["rules"])

    player_name = p.get("name") or "玩家"
    parts.append("【玩家角色】\n" + player_name + "：" + (p.get("role_description") or "（未设定）"))
    if state.is_dead:
        parts.append("【已确定的生命状态】\n玩家已死亡，这段人生已经结束。不得恢复生命、复活或继续行动。死因：" + (p.get("death_cause") or "生命耗尽"))
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
        tnames = "；".join((str(t.get("name") or "") + "：" + str(t.get("description") or "")) if isinstance(t, dict) else str(t) for t in talents)
        parts.append("【玩家天赋】\n" + tnames + "\n这些天赋会影响系统判定，叙事只体现已有作用，不写数值，不把天赋解释成永远不会死亡。")

    physique = d.get("player", {}).get("physique") or {}
    if physique.get("name"):
        parts.append(
            "【主角体质】\n" + str(physique.get("name")) + "：" + str(physique.get("description") or "") +
            "\n这是主角的先天体质，会影响其能力与各类判定；叙事时体现其特点与影响，不要写出数值。"
        )

    p_all = d.get("player", {})
    protection_limits = death_protection_limits(p_all)
    if protection_limits:
        used = p_all.get("death_protections_used") or {}
        counts = "；".join(name + "剩余" + str(max(0, limit - used.get(name, 0))) + "次" for name, limit in protection_limits.items())
        parts.append("【系统已实现的保命能力】\n" + counts + "。仅能在致命战斗伤害时由系统触发，不能阻止无保护的坠崖等环境死亡。次数已经写入存档，不因读档后叙述、模糊冷却或时间推进自动补充。只有系统宣布触发时才能叙述保命效果；不能临时发明其他护佑或复活。")
    else:
        parts.append("【保命能力】\n本角色没有系统已实现且有剩余次数的被动保命效果。不能根据一个强大天赋的名字临时发明免死或复活。")
    golden_fingers = p_all.get("golden_fingers") or []
    if not isinstance(golden_fingers, list):
        golden_fingers = [p_all.get("golden_finger")] if p_all.get("golden_finger") else []
    if golden_fingers:
        gf_desc = "；".join(
            str(g.get("name")) + "：" + str(g.get("description") or "") for g in golden_fingers if isinstance(g, dict)
        )
        parts.append(
            "【金手指】\n" + gf_desc +
            "\n这是主角独有的外挂/优势，叙事时要充分体现其作用，让主角借此占到明显优势；但效果仍需符合世界逻辑，不能凭空解决一切。"
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


RECALL_MAX_CHARS = 1200   # 「相关往事」块的最大字符数（路线图建议 ≤1500 token）


def build_recall_block(items, max_chars=RECALL_MAX_CHARS):
    """把 storage.recall_for_turn 的结果排成提示词段落；超过预算就截断。"""
    lines, used = [], 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        turn_no = item.get("turn_no")
        line = ("- [第 %s 回] %s" % (turn_no, text)) if turn_no else "- " + text
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def build_turn_messages(state, memory, latest_input, recalled=None):
    """构建每轮的用户消息：状态摘要 + 相关往事 + 最近几轮 + 最新输入。

    recalled：storage.recall_for_turn 返回的列表（可选）。放在最近几轮之前、
    状态摘要之后，既补上「很久以前但相关」的事，又不打乱「最近几轮」的原始顺序。
    """
    summary = state.to_summary()

    recent = memory.recent
    if recent:
        recent_block = "\n".join(
            ("玩家：" if t["role"] == "user" else "主持人：") + t["content"] for t in recent
        )
    else:
        recent_block = "（无，游戏刚开始）"

    parts = ["【当前世界状态摘要】\n" + summary]
    recall_block = build_recall_block(recalled or [])
    if recall_block:
        parts.append("【相关往事（系统按相关度检索，可能有用，不一定全部相关）】\n" + recall_block)
    parts.append("【最近几轮对话】\n" + recent_block)
    parts.append("【玩家最新输入】\n" + latest_input)
    user_content = "\n\n".join(parts)
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
