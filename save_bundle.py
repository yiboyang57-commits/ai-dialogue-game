"""存档打包：导出/导入完整存档（世界状态 + 对话历史）。

网页版（Streamlit）部署到云端后，服务器磁盘是临时的，无法长期保存，
因此提供「导出存档」把 state + memory 打成一个 JSON 文件下载到玩家自己设备，
「导入存档」再把该文件读回来恢复进度。CLI/GUI 不受影响（它们仍直接读写本地 save/ 目录）。
"""
import json
import time

BUNDLE_FORMAT = "dsh-save"
BUNDLE_VERSION = 1


def dump_bundle(state, memory):
    """把世界状态 + 对话历史打包成可导出/下载的 dict。"""
    return {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "state": state.data,
        "memory": memory.to_dict(),
    }


def bundle_json(state, memory):
    """返回打包后的 JSON 字符串（用于下载）。"""
    return json.dumps(dump_bundle(state, memory), ensure_ascii=False, indent=2)


def load_bundle(raw):
    """解析存档包（JSON 文本或字节），返回 (state_data, memory_dict)。

    兼容两种形态：
    - 完整存档包：{"format": "dsh-save", "state": {...}, "memory": {...}}
    - 纯状态文件：直接把 state.json 的内容传进来（memory 为空）。
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = raw
    if not isinstance(data, dict):
        raise ValueError("存档文件格式错误：不是 JSON 对象。")

    if "state" in data and isinstance(data["state"], dict):
        state = data["state"]
        memory = data.get("memory") if isinstance(data.get("memory"), dict) else {}
    else:
        # 容错：直接把整个对象当作世界状态
        state = data
        memory = {}

    # 基本的字段完整性校验（避免导入一个无关 JSON 导致崩溃）
    if not isinstance(state.get("meta"), dict) or "player" not in state:
        raise ValueError("存档文件里没有找到有效的世界状态（缺少 meta / player 字段）。")
    return state, memory
