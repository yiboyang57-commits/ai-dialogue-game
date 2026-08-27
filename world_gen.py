"""世界观设定：AI 自定义生成 + 文件上传，统一归一化为“世界观模板”。

模板结构（与 world_state 中的状态字段一一对应）：
{
  "world":  {"name", "background", "rules", "style"},
  "player": {"name", "role_description", "attributes"?, "status_effects"?},
  "initial_situation": str,
  "location": {"name", "description"},
  "time": str,
  "characters": {名字: {description, attitude_toward_player, location, status}},
  "inventory": [{"name", "quantity", "description"}],
  "plot_flags": {标记: true/"字符串"},
  "current_goal": str,
  "combat": {"field", "player_power", "realm_gap", "curve"}
}
"""
import json
import os

from llm_client import LLMError


# ---------------------------------------------------------------- 工具定义

SETUP_WORLD_TOOL = {
    "type": "function",
    "function": {
        "name": "setup_world",
        "description": (
            "返回一份完整、结构化的单人文字冒险游戏世界观设定。"
            "设定要有明确的核心冲突与悬念，能支撑长期剧情推进。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "world": {
                    "type": "object",
                    "description": "世界基本信息。",
                    "properties": {
                        "name": {"type": "string", "description": "世界/背景名称。"},
                        "background": {"type": "string", "description": "世界观背景，2~5 句话，交代时代、环境与核心冲突。"},
                        "rules": {"type": "string", "description": "这个世界的重要规则/设定，如力量体系、禁忌、代价等；可为空。"},
                        "style": {"type": "string", "description": "与世界观匹配的叙事风格：语气、文风、节奏与氛围，供主持人叙事时参考（例如“冷峻写实的侦探小说笔法”“轻快幽默的武侠传奇腔”）。"},
                    },
                    "required": ["background"],
                },
                "player": {
                    "type": "object",
                    "description": "玩家角色。",
                    "properties": {
                        "name": {"type": "string", "description": "角色姓名/称呼；可留空，用“你”代替。"},
                        "role_description": {"type": "string", "description": "角色身份、能力与处境的简要描述。"},
                        "attributes": {
                            "type": "object",
                            "description": "初始属性，如 {\"hp\": 100, \"境界\": \"炼气一层\"}；hp 建议必填。",
                            "additionalProperties": True,
                        },
                    },
                    "required": ["role_description"],
                },
                "talents": {
                    "type": "array",
                    "description": (
                        "玩家开局天赋列表（可为空数组）。天赋会影响各类判定的成功率，"
                        "例如“天命之子”“福星高照”“巧舌如簧”等。"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "天赋名。"},
                            "description": {"type": "string", "description": "天赋效果简述。"},
                        },
                        "required": ["name"],
                    },
                },
                "initial_situation": {
                    "type": "string",
                    "description": "开场情境：玩家此刻身处何地、正在发生什么，一句话到三句话，要有代入感和悬念。",
                },
                "location": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                    },
                },
                "time": {"type": "string", "description": "游戏内起始时间。"},
                "characters": {
                    "type": "object",
                    "description": "开局已知的关键角色（可为空对象）。键为角色名。",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "attitude_toward_player": {"type": "string"},
                            "location": {"type": "string"},
                            "status": {"type": "string"},
                        },
                    },
                },
                "inventory": {
                    "type": "array",
                    "description": "玩家初始背包（可为空数组）。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "quantity": {"type": "integer"},
                            "description": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                },
                "plot_flags": {
                    "type": "object",
                    "description": "开局剧情标记（可为空对象），值为布尔或字符串。",
                },
                "current_goal": {"type": "string", "description": "玩家当前主要目标。"},
                "combat": {
                    "type": "object",
                    "description": "战力体系锚定：用于系统化的战斗胜负判定（判定过程对玩家隐藏）。",
                    "properties": {
                        "field": {"type": "string", "description": "战力体系的量纲名称，如“修仙境界”“战力值”“等级”。"},
                        "player_power": {"type": "number", "description": "主角初始战力数值（与 field 同一量纲）。"},
                        "realm_gap": {"type": "number", "description": "“一个大段位”对应的战力数值差，如 5 表示每高一个大段位战力多 5。"},
                        "curve": {"type": "string", "description": "战斗拟合曲线名；不填则默认 realm_gap。"},
                    },
                    "required": ["field", "player_power", "realm_gap"],
                },
            },
            "required": ["world", "player", "initial_situation"],
        },
    },
}

_GEN_SYSTEM = (
    "你是一个文字冒险游戏的世界观/剧本设定生成器。"
    "根据用户给出的主题与关键词，构思一个适合单人游玩的完整世界观，"
    "并通过调用 setup_world 工具返回结构化设定。设定要新颖、有核心冲突与悬念，能支撑长期剧情。"
)


# ---------------------------------------------------------------- 提示词

def build_gen_messages(theme, keywords, role_hint):
    parts = []
    parts.append("主题/类型：" + (theme or "（由你自选一个）"))
    if keywords:
        parts.append("关键词与要求：" + keywords)
    if role_hint:
        parts.append("玩家希望扮演的角色类型：" + role_hint)
    else:
        parts.append("玩家希望扮演的角色类型：（由你设定一个适合该世界的角色）")
    user = "请生成世界观设定：\n" + "\n".join(parts) + "\n\n调用 setup_world 工具返回完整设定。"
    return [
        {"role": "system", "content": _GEN_SYSTEM},
        {"role": "user", "content": user},
    ]


def build_structure_messages(text, role_hint):
    extra = ("\n\n玩家希望扮演的角色类型：" + role_hint) if role_hint else ""
    user = (
        "下面是一段用户提供的世界观设定文本，请把它整理成结构化设定并调用 setup_world 返回。\n"
        "要求：忠实保留原文的核心设定与细节，不要凭空新增重大设定；"
        "可适度补足开场情境、初始目标等必要的起承转合，使游戏可以直接开始。\n\n"
        "【用户设定文本】\n" + text + extra
    )
    return [
        {"role": "system", "content": _GEN_SYSTEM},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------- 生成 / 结构化

def _call_setup_world(llm, messages):
    content, name, args = llm.call_tool(messages, SETUP_WORLD_TOOL)
    if name == "setup_world" and args:
        return args
    # 模型没调用工具：带一次明确要求重试
    retry = messages + [
        {"role": "assistant", "content": content or ""},
        {"role": "user", "content": "请务必调用 setup_world 工具返回结构化设定。"},
    ]
    content, name, args = llm.call_tool(retry, SETUP_WORLD_TOOL)
    if name != "setup_world" or not args:
        raise LLMError("生成世界观失败：模型未返回 setup_world 工具调用。")
    return args


def generate_world(llm, theme="", keywords="", role_hint=""):
    """AI 根据主题/关键词生成世界观，返回归一化后的模板。"""
    args = _call_setup_world(llm, build_gen_messages(theme, keywords, role_hint))
    return normalize_template(args)


def structure_text(llm, text, role_hint=""):
    """把用户上传的文本整理为结构化模板。"""
    args = _call_setup_world(llm, build_structure_messages(text, role_hint))
    return normalize_template(args)


# ---------------------------------------------------------------- 文件上传

def load_template_from_file(path):
    """从文件读取模板：JSON 直接解析；文本返回原文（由调用方决定是否 AI 结构化）。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在：{path}")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if path.lower().endswith(".json"):
        return load_template_from_json_text(content)
    return load_template_from_text(content)


def load_template_from_json_text(content):
    data = json.loads(content)
    return normalize_template(data)


def load_template_from_text(content):
    """文本上传：返回一个“待结构化”的原始文本（后续交给 AI）。"""
    text = content.strip()
    if not text:
        raise ValueError("文件内容为空。")
    return {"__raw_text__": text}


# ---------------------------------------------------------------- 归一化

def normalize_template(data):
    """把各种来源的设定归一化为标准模板，容忍常见字段别名与类型差异。"""
    if not isinstance(data, dict):
        raise ValueError("世界观模板必须是 JSON 对象。")

    if "__raw_text__" in data:
        raise ValueError("这是待结构化的原始文本，请先交给 AI 结构化。")

    w = data.get("world")
    if isinstance(w, str):
        w = {"background": w}
    w = w if isinstance(w, dict) else {}

    p = data.get("player")
    if isinstance(p, str):
        p = {"role_description": p}
    p = p if isinstance(p, dict) else {}

    loc = data.get("location")
    if isinstance(loc, str):
        loc = {"name": loc}
    loc = loc if isinstance(loc, dict) else {}

    attrs = p.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}
    attrs = dict(attrs)
    attrs.setdefault("max_hp", 100)
    attrs.setdefault("hp", attrs["max_hp"])  # 战斗需要生命值，缺省 100

    talents = p.get("talents") or data.get("talents") or []
    if not isinstance(talents, list):
        talents = [talents]

    combat = data.get("combat")
    combat = combat if isinstance(combat, dict) else {}

    # 保留玩家自定义上传的额外字段，不丢弃、不强套固定模板
    known = {"world", "player", "initial_situation", "opening", "location", "time", "characters",
             "inventory", "plot_flags", "current_goal", "combat", "talents", "role", "background"}
    custom = {k: v for k, v in data.items() if k not in known}
    world_extra = {k: v for k, v in w.items() if k not in ("name", "background", "rules", "style")}
    if world_extra:
        custom["world_extra"] = world_extra

    template = {
        "world": {
            "name": w.get("name", "") or "",
            "background": w.get("background", "") or data.get("background", "") or "",
            "rules": w.get("rules", "") or "",
            "style": w.get("style", "") or "",
        },
        "player": {
            "name": p.get("name", "") or "",
            "role_description": p.get("role_description", "") or data.get("role", "") or "",
            "attributes": attrs,
            "status_effects": p.get("status_effects") or [],
            "talents": talents,
            "physique": p.get("physique") or None,
            "golden_finger": p.get("golden_finger") or None,
        },
        "initial_situation": data.get("initial_situation", "") or data.get("opening", "") or "",
        "location": {
            "name": loc.get("name", "") or "",
            "description": loc.get("description", "") or "",
        },
        "time": data.get("time", "") or "",
        "characters": data.get("characters") or {},
        "inventory": data.get("inventory") or [],
        "plot_flags": data.get("plot_flags") or {},
        "current_goal": data.get("current_goal", "") or "",
        "combat": {
            "field": combat.get("field", "战力") or "战力",
            "player_power": combat.get("player_power", 10) if isinstance(combat.get("player_power"), (int, float)) else 10,
            "realm_gap": combat.get("realm_gap", 5) if isinstance(combat.get("realm_gap"), (int, float)) else 5,
            "curve": combat.get("curve", "realm_gap") or "realm_gap",
        },
        "custom": custom,
    }

    # 柔性校验：背景/初始情境/自定义内容至少其一，否则无法开局
    if not (template["world"]["background"] or template["initial_situation"] or custom):
        raise ValueError("世界观模板为空（至少需要 world.background、initial_situation 或其它自定义内容之一）。")
    return template


# ---------------------------------------------------------------- 开场加载语

# 按世界观关键词匹配的“开场构思中”提示语（放在 game.start() 前的等待文案）
_GENRE_LOADING = [
    (("修仙", "仙侠", "玄幻", "修真", "灵气", "境界", "飞升", "修炼", "金丹", "元婴", "道", "仙"), "天道演变中…"),
    (("末世", "丧尸", "废土", "末日", "灾变", "求生"), "末日钟声敲响中…"),
    (("赛博", "科幻", "星际", "宇宙", "机甲", "未来", "太空", "人工智能"), "星轨校准中…"),
    (("蒸汽", "机械", "齿轮", "工业", "锅炉"), "齿轮转动中…"),
    (("悬疑", "侦探", "推理", "恐怖", "惊悚", "迷雾", "失踪", "灵异"), "迷雾渐浓中…"),
    (("王朝", "宫廷", "古风", "古代", "江湖", "武侠", "皇帝", "太子", "朝堂", "江湖"), "王朝更迭中…"),
    (("都市", "现代", "职场", "校园", "商战", "都市传说"), "霓虹渐起中…"),
    (("魔法", "奇幻", "西幻", "龙", "精灵", "巫师", "中世纪", "炼金"), "星辉流转中…"),
]


def world_loading_phrase(template):
    """根据世界观背景/规则/战力量纲，返回一句贴合题材的“构思中”文案。"""
    w = template.get("world") if isinstance(template, dict) else None
    w = w if isinstance(w, dict) else {}
    combat = template.get("combat") if isinstance(template, dict) else None
    combat = combat if isinstance(combat, dict) else {}
    text = " ".join(filter(None, [
        w.get("name", ""), w.get("background", ""), w.get("rules", ""),
        w.get("style", ""), combat.get("field", ""),
    ]))
    for keywords, phrase in _GENRE_LOADING:
        if any(k in text for k in keywords):
            return phrase
    return "世界徐徐展开中…"


# ---------------------------------------------------------------- 预览

def render_preview(t):
    """把模板渲染成人类可读的预览。"""
    lines = []
    w = t["world"]
    if w.get("name"):
        lines.append(f"世界名称：{w['name']}")
    lines.append(f"背景：{w.get('background') or '（无）'}")
    if w.get("rules"):
        lines.append(f"规则：{w['rules']}")
    if w.get("style"):
        lines.append(f"叙事风格：{w['style']}")

    p = t["player"]
    name = p.get("name") or "玩家"
    lines.append(f"玩家角色：{name} —— {p.get('role_description') or '（未设定）'}")
    attrs = p.get("attributes") or {}
    if attrs:
        lines.append("初始属性：" + "、".join(f"{k}={v}" for k, v in attrs.items()))
    talents = p.get("talents") or []
    if talents:
        tnames = "、".join(t.get("name") if isinstance(t, dict) else str(t) for t in talents)
        lines.append(f"天赋：{tnames}")
    physique = p.get("physique") or {}
    golden_finger = p.get("golden_finger") or {}
    if physique.get("name") or golden_finger.get("name"):
        lines.append(f"体质：{physique.get('name') or '无'}；金手指：{golden_finger.get('name') or '无'}")

    lines.append(f"初始情境：{t.get('initial_situation') or '（无）'}")

    loc = t.get("location") or {}
    if loc.get("name"):
        loc_line = f"起始地点：{loc['name']}"
        if loc.get("description"):
            loc_line += f"（{loc['description']}）"
        lines.append(loc_line)

    if t.get("time"):
        lines.append(f"时间：{t['time']}")

    if t.get("characters"):
        lines.append("初始角色：")
        for n, info in t["characters"].items():
            line = f"  · {n}"
            if isinstance(info, dict) and info.get("description"):
                line += f"：{info['description']}"
            lines.append(line)

    if t.get("inventory"):
        items = "、".join(f"{i.get('name')}×{i.get('quantity', 1)}" for i in t["inventory"])
        lines.append(f"初始背包：{items}")

    if t.get("plot_flags"):
        lines.append("剧情标记：" + "，".join(f"{k}={v}" for k, v in t["plot_flags"].items()))

    if t.get("current_goal"):
        lines.append(f"初始目标：{t['current_goal']}")

    c = t.get("combat") or {}
    if c:
        lines.append(
            f"战力体系：{c.get('field', '战力')}，主角战力 {c.get('player_power')}，段位差 {c.get('realm_gap')}"
        )

    return "\n".join(lines)
