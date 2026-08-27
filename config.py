"""配置加载：API Key、模型名、存档目录与记忆/压缩参数。"""
import json
import os
import sys

# DeepSeek OpenAI 兼容接口
BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

# 项目根目录：PyInstaller 打包(frozen)后，config.json/save 放在可执行文件同级目录；
# 源码运行时用本文件所在目录。
if getattr(sys, "frozen", False):
    ROOT_DIR = os.path.dirname(sys.executable)
else:
    ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")
SAVE_DIR = os.path.join(ROOT_DIR, "save")


def cacert_path():
    """CA 证书 bundle 路径（打包后从 _MEIPASS 读取，源码运行时在项目根目录）。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", ROOT_DIR)
    else:
        base = ROOT_DIR
    return os.path.join(base, "cacert.pem")


def save_slot_suffix(save_key=""):
    """把存档名转成安全的后缀（空返回 ""），用于状态/记忆文件命名空间。

    存档名保留字母/数字/中文/连字符/下划线，其余字符被丢弃。
    """
    key = (save_key or "").strip()
    if not key:
        return ""
    safe = "".join(ch for ch in key if ch.isalnum() or ch in "-_")
    return "_" + (safe or "slot")


def list_save_slots():
    """列出 save/ 目录下已有的存档名（从 state_*.json 推导），不含默认槽。"""
    slots = []
    if not os.path.isdir(SAVE_DIR):
        return slots
    prefix = "state_"
    for fn in sorted(os.listdir(SAVE_DIR)):
        if fn.startswith(prefix) and fn.endswith(".json"):
            mid = fn[len(prefix):-len(".json")]
            if mid:
                slots.append(mid)
    return slots

# ---- 记忆与历史压缩参数 ----
KEEP_RECENT_ENTRIES = 6    # 每轮(玩家+主持人)2条，这里保留最近3轮完整对话
COMPRESS_EVERY_TURNS = 20  # 每 20 轮自动压缩一次历史
MAX_EVENT_LOG = 15         # 事件日志最多保留条数
MAX_SUMMARY_CHARS = 400    # 故事摘要建议最大长度（仅作提示用）
MAX_CHAPTER_SUMMARIES = 6  # 章节摘要最多保留条数（长局记忆，超出滚动淘汰）


def load_api_key():
    """优先读环境变量 DEEPSEEK_API_KEY，其次读 config.json。"""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key and key.strip():
        return key.strip()

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            key = data.get("api_key") or data.get("DEEPSEEK_API_KEY")
            if key and key.strip():
                return key.strip()
        except (json.JSONDecodeError, OSError):
            pass
    return None


def save_api_key(key):
    """把 Key 写入 config.json（已存在则合并，避免覆盖 model 等字段）。"""
    data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}
    data["api_key"] = key.strip()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_model():
    """读取模型名，缺省 deepseek-chat。"""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("model") or DEFAULT_MODEL
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_MODEL
