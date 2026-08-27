"""工具定义：update_world_state 的 JSON Schema，以及更新合并入口。

模型不通过“纯文本描述状态变化 + 正则解析”，而是显式调用该工具；
我们直接拿到结构化的 JSON 增量并合并进世界状态。
"""
from world_state import apply_update  # noqa: E402  (导入放底部仅为集中导出)


UPDATE_TOOL = {
    "type": "function",
    "function": {
        "name": "update_world_state",
        "description": (
            "在完成本轮叙事之后，用结构化方式更新持久化的世界状态。"
            "只填写本轮发生变化的部分；未变化的字段可以省略，系统会自动保留原值。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "玩家当前所在地点名称（若发生变化）。",
                },
                "location_description": {
                    "type": "string",
                    "description": "当前地点的简要描述（若发生变化）。",
                },
                "time": {
                    "type": "string",
                    "description": "游戏内时间（若推进，例如“第二天清晨”）。",
                },
                "player_attributes": {
                    "type": "object",
                    "description": (
                        "玩家属性变化，只包含变化的键，值可以是数字或字符串，"
                        '例如 {"hp": 90, "境界": "筑基初期"}。'
                    ),
                    "additionalProperties": True,
                },
                "player_status_effects": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "玩家当前全部状态效果（完整覆盖，如受伤/中毒/疲惫；没有则给空数组）。",
                },
                "player_power": {
                    "type": "number",
                    "description": "玩家战力提升后的新数值（突破/升级等里程碑时更新，覆盖旧值）。",
                },
                "characters": {
                    "type": "object",
                    "description": (
                        "发生变化的角色，键为角色名。可包含 description / attitude_toward_player(文字，如“友善”) / "
                        "location / status 等字段，缺失字段保持原值。"
                        "好感度请优先用 relationship_events 记录事件让系统换算，不要在这里写 affinity 数值。"
                    ),
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "attitude_toward_player": {"type": "string"},
                            "location": {"type": "string"},
                            "status": {"type": "string"},
                            "affinity": {"type": "number", "description": "该角色对玩家的好感度（0~100，用于关系判定）。"},
                        },
                    },
                },
                "inventory_add": {
                    "type": "array",
                    "description": "本轮获得/增加的道具或货币（货币也是道具，用数量表示，如“金币”数量+50）。",
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
                "inventory_remove": {
                    "type": "array",
                    "description": (
                        "本轮消耗/失去的道具或货币。必须给出数量，系统按数量扣减，"
                        "不会整类删除（除非 remove_all=true）。"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "quantity": {"type": "integer", "description": "扣减数量，默认 1。"},
                            "remove_all": {"type": "boolean", "description": "为 true 时移除该物品全部（仅用于彻底丢弃某物，货币不要用）。"},
                        },
                        "required": ["name"],
                    },
                },
                "plot_flags": {
                    "type": "object",
                    "description": "需要设置/更新的剧情标记（键为标记名，值为布尔或字符串），会与现有标记合并。",
                },
                "current_goal": {
                    "type": "string",
                    "description": "玩家当前主要目标（若发生变化）。",
                },
                "combat": {
                    "type": "object",
                    "description": (
                        "当发生战斗/追击/逃命等需要分胜负的冲突时，必须填写此项："
                        "声明敌方与玩家意图，由系统判定结果。主持人只描述起因与过程，"
                        "不得自行决定战斗结局。"
                    ),
                    "properties": {
                        "enemy_name": {"type": "string", "description": "敌人名称。"},
                        "enemy_power": {"type": "number", "description": "敌人战力数值（与战力体系同一量纲）。"},
                        "action": {
                            "type": "string",
                            "enum": ["fight", "flee"],
                            "description": "玩家本次意图：fight=迎战，flee=逃跑。",
                        },
                        "use_item": {
                            "type": "string",
                            "description": "若玩家主动使用某件法宝/一次性道具（如“爆元丹”“护身符”），填道具名；否则省略。",
                        },
                    },
                    "required": ["enemy_name", "enemy_power", "action"],
                },
                "judgment": {
                    "type": "object",
                    "description": (
                        "当出现需要判定成败的非战斗情境（建立亲密关系、寻宝、说服、潜行、洞察、"
                        "炼制、危险行为等）时，必须填写此项，由系统判定结果；主持人不得自行决定成败。"
                    ),
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["relationship", "treasure", "persuasion", "insight", "sneak", "craft", "action"],
                            "description": "判定类别。",
                        },
                        "subject": {"type": "string", "description": "判定对象/目标，如 NPC 姓名或事件简述。"},
                        "factors": {
                            "type": "object",
                            "description": (
                                "影响因素及其有利程度（-3~+3 整数，负为不利，正为有利）。"
                                "只填与本次判定相关的因素名，如 当前好感度 / 场景氛围契合 / 福缘运气 / 线索完整度 等。"
                            ),
                            "additionalProperties": {"type": "number"},
                        },
                        "use_item": {"type": "string", "description": "若主动使用某一次性道具，填道具名；否则省略。"},
                    },
                    "required": ["type"],
                },
                "relationship_events": {
                    "type": "array",
                    "description": (
                        "本轮发生的关系事件，用于让好感度动态增减（系统按事件类型自动换算，无需写数值）。"
                        "例如与某人并肩作战、救命之恩、赠礼、冒犯、误会、背弃等。"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "subject": {"type": "string", "description": "关系对象（角色名）。"},
                            "event": {"type": "string", "description": "事件标签，如 救命之恩/并肩作战/共同经历/赠礼/相谈甚欢/冒犯/误会/欺骗/背弃。"},
                        },
                        "required": ["subject", "event"],
                    },
                },
                "attribute_events": {
                    "type": "array",
                    "description": (
                        "其它系数的动态增减（声望/魅力/福缘/体力/修为等）。"
                        "可用事件标签（如“声望大涨”），或直接写 {\"attribute\":\"声望\",\"delta\":10}。"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "event": {"type": "string", "description": "事件标签，如 声望大涨/声名狼藉/魅力大增/精疲力竭/修为精进。"},
                            "attribute": {"type": "string", "description": "要增减的系数名。"},
                            "delta": {"type": "integer", "description": "增减量（正负均可）。"},
                        },
                    },
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "本轮结束后，为玩家列出 2~4 个“显而易见”的下一步行动选项，帮助其游玩。"
                        "选项必须是玩家根据当前情境自然就能想到的常见行动，不得包含隐藏线索或隐藏剧情触发；玩家也可自定义输入其它行动。"
                    ),
                },
                "turn_event": {
                    "type": "string",
                    "description": "用一句话记录本轮关键事件，用于状态摘要。",
                },
            },
        },
    },
}
