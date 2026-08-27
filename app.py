"""AI 对话模拟器 · Streamlit 精致版界面。

运行：streamlit run app.py
复用命令行/图形版的核心逻辑（game.py / world_state.py / judgment.py …），
这里只做界面呈现层：三区布局 + 聊天气泡 + 卡片化世界状态，视觉走简洁白灰 + 单一强调色。
"""
import html
import random

import streamlit as st

from config import BASE_URL, load_api_key, save_api_key, load_model, list_save_slots
from game import Game
from llm_client import LLMClient
from memory import Memory
from world_state import WorldState
import character_builder
import dynamics
import save_bundle
import world_gen

# ---------------------------------------------------------------- 主题与全局样式
# 暖色浅色主题（与桌面版 gui.py 保持一致）：陶土橙 + 暖米色
VERSION = "v2.11"  # 界面版本号：用于确认云端是否已部署最新代码（顶栏可见）
CHAT_HEIGHT = 500  # 聊天区固定高度（像素），内部滚动，输入框/面板保持不动

st.set_page_config(page_title="文字冒险 · Game Master", page_icon="🗡️", layout="wide")

st.markdown("""
<style>
/* ===== 基础：暖米色背景 + 中文友好字体 ===== */
html, body, .stApp {
    font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans SC", sans-serif;
}
.stApp { background: #f5efe6; color: #4a4136; }
/* 顶部留白：避开 Streamlit 顶部工具栏 + iOS 刘海安全区，防止内容被遮挡 */
.block-container {
    padding-top: calc(3.4rem + env(safe-area-inset-top, 0px));
    padding-bottom: 4rem;
    max-width: 1320px;
}
/* Streamlit 顶部工具栏改成暖色实底，滚动时不会透出底下内容 */
header[data-testid="stHeader"] {
    background: rgba(245,239,230,.96) !important;
    border-bottom: 1px solid #e8dcc7;
    backdrop-filter: blur(6px);
}

/* ===== 顶栏 ===== */
.gm-header {
    display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px;
    background: linear-gradient(135deg, #fffaf3 0%, #f7ecdd 100%);
    border:1px solid #e8dcc7; border-radius:16px; padding:14px 20px; margin-bottom:14px;
    box-shadow: 0 2px 12px rgba(192,105,63,.10);
}
.gm-brand { font-size:20px; font-weight:800; color:#4a4136; letter-spacing:.4px; display:flex; align-items:center; }
.gm-icon { font-size:24px; margin-right:8px; }
.gm-sub { font-size:13px; color:#9a8e7d; margin-left:10px; font-weight:400; }
.gm-ver { display:inline-block; font-size:11px; color:#fff; background:#d67c4e; border-radius:999px; padding:2px 10px; margin-left:10px; font-weight:700; }
.gm-turn { font-size:13px; color:#9a8e7d; }

/* ===== 聊天气泡 ===== */
.narrator-label { font-size:12px; color:#b8ad9c; margin:0 0 3px 2px; }
.narrator-bubble { background:#f0e7d6; border:1px solid #e8dcc7; border-radius:14px;
    padding:11px 15px; max-width:82%; font-size:16px; line-height:1.85; color:#4a4136;
    margin:2px 0 18px 0; }
.thinking-bubble { background:#fbf6ec; border:1px dashed #d9c7ac; border-radius:14px;
    padding:9px 14px; max-width:82%; font-size:14px; line-height:1.6; color:#9a8e7d; font-style:italic;
    margin:2px 0 18px 0; }
.player-label { font-size:12px; color:#b8ad9c; text-align:right; margin:0 2px 3px 0; }
.player-row { display:flex; justify-content:flex-end; margin:2px 0 18px 0; }
.player-bubble { background:#f7e3d3; border:1px solid #eecfb8; border-radius:14px;
    padding:11px 15px; max-width:72%; font-size:15px; line-height:1.75; color:#5b3a27; }

/* ===== 右侧状态卡片 ===== */
.gm-panel-top { font-size:13px; color:#6b5f52; padding:2px 4px 10px 4px; }
.gm-card { background:#fffaf3; border:1px solid #e8dcc7; border-radius:14px;
    padding:14px 16px; margin-bottom:12px; box-shadow: 0 1px 6px rgba(192,105,63,.05); }
.gm-card-title { font-size:13px; font-weight:800; color:#c0693f; margin-bottom:10px; letter-spacing:.5px; }
.char-row { display:flex; align-items:flex-start; padding:7px 0; border-bottom:1px dashed #eee2d0; }
.char-row:last-child { border-bottom:none; }
.avatar { width:36px; height:36px; border-radius:50%; background:#d67c4e; color:#fff;
    display:flex; align-items:center; justify-content:center; font-size:15px; font-weight:700; flex:none; margin-right:10px; }
.char-name { font-size:14px; font-weight:700; color:#4a4136; }
.char-sub { font-size:12px; color:#6b5f52; margin-top:1px; line-height:1.5; }
.char-loc { font-size:12px; color:#9a8e7d; margin-top:2px; }
.chip { display:inline-block; background:#f6ecdd; border:1px solid #e8dcc7; border-radius:999px;
    padding:3px 11px; margin:3px 5px 3px 0; font-size:12px; color:#5b3a27; }
.bullet { font-size:13px; color:#4a4136; padding:2px 0; line-height:1.65; }
.bullet b { color:#c0693f; font-weight:700; }
.muted { font-size:12px; color:#9a8e7d; }

/* ===== 输入框 / 按钮 ===== */
div[data-testid="stTextInput"] input, div[data-testid="stTextArea"] textarea {
    background:#fffdf8 !important; color:#4a4136 !important;
    border:1px solid #e8dcc7 !important; border-radius:10px !important;
}
div[data-testid="stTextInput"] input:focus, div[data-testid="stTextArea"] textarea:focus {
    border-color:#d67c4e !important; box-shadow:0 0 0 2px rgba(214,124,78,.18) !important;
}
div[data-testid="stChatInput"] textarea { border-radius:12px !important; }
div[data-testid="stChatInput"] textarea:focus { border-color:#d67c4e !important; box-shadow:0 0 0 2px rgba(214,124,78,.18) !important; }
.stButton > button { border-radius:10px; font-weight:600; }
.stButton > button:hover { opacity:.92; transform:translateY(-1px); }
.stButton > button[kind="primary"] { background:#d67c4e; border-color:#d67c4e; color:#fff; }
.stButton > button[kind="primary"]:hover { background:#c0693f; border-color:#c0693f; color:#fff; }

/* ===== 侧边栏 ===== */
section[data-testid="stSidebar"] { background:#f3ead9; border-right:1px solid #e8dcc7; }
section[data-testid="stSidebar"] .stDownloadButton > button { background:#d67c4e; border-color:#d67c4e; color:#fff; border-radius:10px; }

/* ===== 卡片式表单容器（st.container(border=True)）===== */
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-color:#e8dcc7 !important;
    border-radius:14px !important;
    background:#fffaf3;
}

/* ===== 滚动条 ===== */
::-webkit-scrollbar { width:10px; height:10px; }
::-webkit-scrollbar-thumb { background:#d9c7ac; border-radius:8px; }
::-webkit-scrollbar-thumb:hover { background:#c9b291; }

/* ===== 移动端：卡片网格 3 列 → 2 列，缩短选天赋界面 ===== */
@media (max-width: 768px) {
    div[data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
        min-width: 50% !important; max-width: 50% !important;
    }
}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- 工具函数

def esc(text):
    return html.escape(str(text)).replace("\n", "<br>")


def header_html(turn=None):
    """统一的顶部标题栏 HTML（含版本号，方便确认云端是否最新）。"""
    right = ""
    if turn is not None:
        right = f'<span class="gm-turn">第 {turn} 回合</span>'
    return (
        '<div class="gm-header"><div class="gm-brand">'
        '<span class="gm-icon">🗡️</span>文字冒险 · Game Master'
        f'<span class="gm-ver">{VERSION}</span>'
        '<span class="gm-sub">AI 对话模拟器</span>'
        '</div>'
        f'<div style="display:flex;align-items:center;gap:12px;">{right}</div>'
        '</div>'
    )


def _secrets_api_key():
    """读取 Streamlit Cloud Secrets 中的 API Key（部署到云端时用）。"""
    try:
        s = st.secrets
        key = None
        for reader in (lambda: s.get("DEEPSEEK_API_KEY"),
                       lambda: s["DEEPSEEK_API_KEY"],
                       lambda: getattr(s, "DEEPSEEK_API_KEY", None)):
            try:
                key = reader()
            except Exception:
                continue
            if key:
                break
        if key and str(key).strip():
            return str(key).strip()
    except Exception:
        pass
    return None


def ensure_llm():
    if "llm" not in st.session_state:
        key = _secrets_api_key() or load_api_key()
        if key:
            st.session_state.llm = LLMClient(api_key=key, model=load_model() or "deepseek-chat", base_url=BASE_URL)
        else:
            st.session_state.llm = None
    return st.session_state.llm


def affinity_label(info, turn):
    aff = info.get("affinity")
    if not isinstance(aff, (int, float)):
        return None
    eff = dynamics.effective_affinity(info, turn)
    if eff >= 70:
        return "好感=高"
    if eff >= 40:
        return "好感=中"
    if eff >= 15:
        return "好感=低"
    return "好感=陌生"


def render_log(log):
    """渲染消息：新的在上，保证刚发送/刚回复的内容立即可见、无需滚动。"""
    for m in reversed(log):
        role = m.get("role")
        if role == "player":
            st.markdown('<div class="player-label">你</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="player-row"><div class="player-bubble">{esc(m["content"])}</div></div>',
                        unsafe_allow_html=True)
        elif role == "thinking":
            st.markdown('<div class="narrator-label">旁白 · 思考中</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="thinking-bubble">{esc(m["content"])}</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="narrator-label">旁白</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="narrator-bubble">{esc(m["content"])}</div>', unsafe_allow_html=True)


def render_world_panel(game):
    d = game.state.data
    turn = d.get("meta", {}).get("turn", 0)

    loc = d.get("location", {}) or {}
    top_bits = []
    if loc.get("name"):
        top_bits.append(f"📍 {loc['name']}")
    if d.get("time"):
        top_bits.append(f"🕐 {d['time']}")
    if top_bits:
        st.markdown(f'<div class="gm-panel-top">{"　".join(top_bits)}</div>', unsafe_allow_html=True)

    # —— 主角卡片 ——
    p = d.get("player", {}) or {}
    st.markdown('<div class="gm-card"><div class="gm-card-title">🧑 主角</div>', unsafe_allow_html=True)
    pbits = []
    name = p.get("name") or "你"
    role = p.get("role_description") or ""
    pbits.append(f'<div class="bullet"><b>{esc(name)}</b>' + (f'：{esc(role)}' if role else "") + "</div>")
    talents = [t.get("name") if isinstance(t, dict) else str(t) for t in (p.get("talents") or [])]
    talents = [t for t in talents if t]
    pbits.append(f'<div class="bullet"><b>天赋</b>：{esc("、".join(talents) if talents else "无")}</div>')
    physique = p.get("physique") or {}
    gf = p.get("golden_finger") or {}
    if physique.get("name") or gf.get("name"):
        pbits.append(f'<div class="bullet"><b>体质</b>：{esc(physique.get("name") or "无")}　<b>金手指</b>：{esc(gf.get("name") or "无")}</div>')
    status = p.get("status_effects") or []
    if status:
        pbits.append(f'<div class="bullet"><b>状态</b>：{esc("、".join(status))}</div>')
    st.markdown("".join(pbits), unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # —— 角色卡片 ——
    chars = d.get("characters") or {}
    st.markdown('<div class="gm-card"><div class="gm-card-title">👥 角色</div>', unsafe_allow_html=True)
    if chars:
        rows = []
        for name, info in chars.items():
            avatar = name[0] if name else "?"
            subs = []
            if info.get("description"):
                subs.append(info["description"])
            aff = affinity_label(info, turn)
            if aff:
                subs.append(aff)
            status = info.get("status")
            if status:
                subs.append(status)
            sub = " · ".join(subs) if subs else "—"
            loc_line = f"<div class='char-loc'>📍 {esc(info.get('location', ''))}</div>" if info.get("location") else ""
            rows.append(
                f"<div class='char-row'><div class='avatar'>{esc(avatar)}</div>"
                f"<div><div class='char-name'>{esc(name)}</div>"
                f"<div class='char-sub'>{esc(sub)}</div>{loc_line}</div></div>"
            )
        st.markdown("".join(rows), unsafe_allow_html=True)
    else:
        st.markdown('<div class="muted">暂无角色</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # —— 物品卡片（chip 平铺）——
    inv = d.get("inventory") or []
    st.markdown('<div class="gm-card"><div class="gm-card-title">🎒 物品</div>', unsafe_allow_html=True)
    if inv:
        chips = "".join(
            f'<span class="chip">{esc(i.get("name"))} ×{i.get("quantity", 1)}</span>' for i in inv
        )
        st.markdown(chips, unsafe_allow_html=True)
    else:
        st.markdown('<div class="muted">空</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # —— 关键剧情卡片 ——
    flags = d.get("plot_flags") or {}
    goal = d.get("current_goal") or ""
    st.markdown('<div class="gm-card"><div class="gm-card-title">🗺️ 关键剧情</div>', unsafe_allow_html=True)
    bullets = []
    if goal:
        bullets.append(f'<div class="bullet"><b>当前目标</b>：{esc(goal)}</div>')
    for k, v in flags.items():
        bullets.append(f'<div class="bullet"><b>{esc(k)}</b>：{esc(str(v))}</div>')
    if bullets:
        st.markdown("".join(bullets), unsafe_allow_html=True)
    else:
        st.markdown('<div class="muted">暂无节点</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


# 主持人“思考链”文案（等待模型回复时在旁白气泡里随机展示）
THINKING_PHRASES = [
    "正在推演剧情走向…",
    "正在斟酌角色反应…",
    "正在编织对白…",
    "正在铺陈氛围…",
    "正在权衡因果…",
    "正在翻找线索…",
    "正在酝酿转折…",
    "正在推敲细节…",
]


def random_think():
    return random.choice(THINKING_PHRASES)


def session_slot():
    """本会话的临时自动存档槽（不同玩家互不覆盖，且不会出现在「继续存档」列表里）。"""
    if "session_slot" not in st.session_state:
        st.session_state.session_slot = "临时-" + str(random.randint(100000, 999999))
    return st.session_state.session_slot


def _save_to_named_slot(name):
    """把当前游戏显式保存到一个命名槽，成功返回 True。"""
    key = (name or "").strip()
    if not key:
        return False
    game = st.session_state.game
    game.state.save_key = key
    game.memory.save_key = key
    game.save()
    st.session_state.current_slot = key
    return True


def _load_named_slot(key):
    """从命名槽读档并切到游戏界面。"""
    state = WorldState.load(key)
    if state is None:
        st.error("该存档不存在或已失效。")
        return
    memory = Memory.load(key)
    game = Game(state, memory, st.session_state.llm)
    st.session_state.game = game
    st.session_state.log = [{"role": "narrator", "content": "（已读取存档）\n" + state.to_player_summary()}]
    st.session_state.current_slot = key
    st.session_state.character_step = False
    st.rerun()


def _stop_world():
    """停止当前世界，清空游戏与构建状态，返回世界观生成界面。"""
    for k in ("game", "log", "current_slot", "pending_input", "character_step", "character_pool",
              "ask_stop", "saving", "stop_after_save", "template",
              "talent_hand", "physique_hand", "gf_hand", "talent_lock", "physique_lock", "gf_lock",
              "talent_sel", "physique_sel", "gf_sel"):
        st.session_state.pop(k, None)
    st.rerun()


def load_into_session(state_data, memory_dict, save_key=""):
    """把世界状态 + 记忆数据装入当前会话并切到游戏界面（用于导入存档）。"""
    state = WorldState(state_data, save_key=save_key)
    memory = Memory.from_dict(memory_dict, save_key=save_key)
    game = Game(state, memory, st.session_state.llm)
    st.session_state.game = game
    st.session_state.log = []
    st.session_state.log.append({
        "role": "narrator",
        "content": "（已读取存档）\n" + state.to_player_summary(),
    })
    st.session_state.character_step = False
    st.rerun()


def start_game(template):
    save_key = session_slot()
    state = WorldState(save_key=save_key)
    state.apply_template(template)
    game = Game(state, Memory(save_key=save_key), st.session_state.llm)
    loading = world_gen.world_loading_phrase(template)
    with st.spinner(loading):
        opening = game.start()
    st.session_state.game = game
    st.session_state.log = []
    st.session_state.log.append({"role": "narrator", "content": opening})
    st.session_state.current_slot = None
    st.session_state.pop("preview", None)
    st.session_state.pop("template", None)
    st.session_state.character_step = False
    st.rerun()


def render_save_sidebar():
    """侧边栏：显式保存（起名+确认）/ 继续存档列表 / 导出导入。"""
    with st.sidebar:
        st.markdown("### 💾 存档")

        game = st.session_state.get("game")
        cur = st.session_state.get("current_slot")

        # 显式保存：点按钮 → 起名 → 确认
        if game is not None:
            if not st.session_state.get("saving"):
                cap = "当前存档：" + (cur or "未命名")
                st.caption(cap)
                if st.button("💾 保存游戏", type="primary", use_container_width=True):
                    st.session_state.saving = True
                    st.rerun()
            else:
                name = st.text_input("给存档起个名字（如 111 / 222）", key="save_name_draft")
                c1, c2 = st.columns(2)
                if c1.button("确认保存", type="primary", use_container_width=True):
                    if _save_to_named_slot(name):
                        st.session_state.saving = False
                        if st.session_state.get("stop_after_save"):
                            _stop_world()
                        else:
                            st.rerun()
                    else:
                        st.warning("名字不能为空")
                if c2.button("取消", use_container_width=True):
                    st.session_state.saving = False
                    st.rerun()

        # 导出 / 导入（跨设备持久化）
        if game is not None:
            data = save_bundle.bundle_json(game.state, game.memory)
            st.download_button(
                "⬇ 导出到设备",
                data=data.encode("utf-8"),
                file_name="存档_" + (cur or "未命名") + ".json",
                mime="application/json",
                use_container_width=True,
            )
        up = st.file_uploader("⬆ 从设备导入", type=["json"], key="import_save")
        if up is not None:
            try:
                state_data, memory_dict = save_bundle.load_bundle(up.getvalue())
            except Exception as e:
                st.error("导入失败：" + str(e))
            else:
                st.session_state.pop("import_save", None)
                load_into_session(state_data, memory_dict, session_slot())

        # 继续存档：列出所有已命名的存档（排除会话临时槽）
        st.divider()
        st.markdown("**继续存档**")
        slots = [s for s in list_save_slots() if not (s["name"] or s["key"]).startswith("临时-")]
        if slots:
            for s in slots:
                label = s["name"] or s["key"]
                if st.button("▶ " + label, key="load_" + s["key"], use_container_width=True):
                    _load_named_slot(s["key"])
        else:
            st.caption("暂无已命名的存档")

        st.caption("云端磁盘是临时的，跨设备请用「导出/导入」。")

        # 停止世界
        if game is not None:
            st.divider()
            if not st.session_state.get("ask_stop"):
                if st.button("🛑 停止世界", use_container_width=True):
                    st.session_state.ask_stop = True
                    st.rerun()
            else:
                st.warning("确定停止当前世界吗？")
                c1, c2, c3 = st.columns(3)
                if c1.button("保存后停止", use_container_width=True):
                    st.session_state.stop_after_save = True
                    st.session_state.saving = True
                    st.rerun()
                if c2.button("不保存", use_container_width=True):
                    _stop_world()
                if c3.button("取消", use_container_width=True):
                    st.session_state.ask_stop = False
                    st.rerun()


def start_build(template):
    """世界观已定，进入主角构建步骤（重置候选池与选择状态）。"""
    st.session_state.template = template
    st.session_state.character_step = True
    st.session_state.character_pool = None
    for k in ("talent_hand", "physique_hand", "gf_hand", "talent_lock", "physique_lock", "gf_lock",
              "talent_sel", "physique_sel", "gf_sel"):
        st.session_state.pop(k, None)
    st.rerun()


def _ensure_hands(pool):
    """确保每个类别的 9 张手牌已生成。"""
    mapping = {"talent": "talents", "physique": "physiques", "gf": "golden_fingers"}
    for cat, key in mapping.items():
        hand_key = cat + "_hand"
        if not st.session_state.get(hand_key):
            st.session_state[hand_key] = character_builder.sample_hand(pool.get(key, []), 9)


def _toggle_lock(cat, name):
    lock_key = cat + "_lock"
    st.session_state[lock_key] = None if st.session_state.get(lock_key) == name else name
    st.rerun()


def _toggle_select(cat, name, is_multi, max_sel):
    sel_key = cat + "_sel"
    if is_multi:
        cur = list(st.session_state.get(sel_key) or [])
        if name in cur:
            cur.remove(name)
        else:
            if len(cur) >= max_sel:
                st.warning(f"最多选 {max_sel} 个。")
                return
            cur.append(name)
        st.session_state[sel_key] = cur
    else:
        st.session_state[sel_key] = None if st.session_state.get(sel_key) == name else name
    st.rerun()


def _render_pool_card(cat, item, idx, locked, sel, is_multi, max_sel):
    name = item["name"]
    tier = item.get("tier") or "蓝"
    style = character_builder.TIER_STYLES.get(tier, character_builder.TIER_STYLES["蓝"])
    is_locked = (locked == name)
    is_sel = (name in sel) if is_multi else (sel == name)

    if tier == "炫彩":
        border_css = "linear-gradient(90deg,#f00,#ff8a00,#ffe600,#2bd64f,#2bd6ff,#7a4fd0,#ff5ad0)"
        left_css = "border-left:4px solid transparent;"
    else:
        border_css = style["border"]
        left_css = f"border-left:4px solid {style['fg']};"

    # 代价角标
    warn_badge = ' <span style="color:#c0392b;">⚠</span>' if item.get("drawback") else ""

    # 描述 + 代价折叠在「详情」里
    details_html = ""
    desc = item.get("description") or ""
    drawback = item.get("drawback") or ""
    if desc or drawback:
        body = ""
        if desc:
            body += f'<div style="font-size:12px;color:#6b5f52;margin-top:4px;">{esc(desc)}</div>'
        if drawback:
            body += f'<div style="font-size:11px;color:#c0392b;margin-top:4px;">⚠ 代价：{esc(drawback)}</div>'
        details_html = (
            f'<details style="margin-top:4px;">'
            f'<summary style="font-size:11px;color:#9a8e7d;cursor:pointer;">详情</summary>{body}</details>'
        )

    st.markdown(
        f'<div style="border:1px solid {border_css}; {left_css} background:{style["bg"]}; '
        f'border-radius:10px; padding:7px 9px; min-height:52px;">'
        f'<div style="font-weight:700; color:{style["fg"]};">{esc(name)}{warn_badge}</div>'
        f'<div style="font-size:11px; color:{style["fg"]}; opacity:.85;">{tier}</div>'
        f'{details_html}'
        f'</div>',
        unsafe_allow_html=True,
    )
    b1, b2 = st.columns(2)
    with b1:
        if st.button("🔒" if is_locked else "🔓", key=f"lock_{cat}_{idx}", use_container_width=True):
            _toggle_lock(cat, name)
    with b2:
        if st.button("✓" if is_sel else "选", key=f"sel_{cat}_{idx}", use_container_width=True):
            _toggle_select(cat, name, is_multi, max_sel)


def _render_pool_picker(cat, title, pool_items, is_multi, max_sel, allow_none=False):
    hand_key = cat + "_hand"
    lock_key = cat + "_lock"
    sel_key = cat + "_sel"
    locked = st.session_state.get(lock_key)

    st.markdown(f"**{title}**")
    c1, c2 = st.columns([1, 4])
    with c1:
        if st.button("🎲 重新roll", key=f"roll_{cat}", use_container_width=True):
            st.session_state[hand_key] = character_builder.sample_hand(pool_items, 9, locked)
            st.rerun()
    with c2:
        if locked:
            st.caption("已锁定：" + locked + "（点卡片 🔒 解锁）")
        else:
            st.caption("可锁定 1 个，再 roll 时它不变")

    if allow_none:
        if st.button("🚫 不携带金手指", key=f"none_{cat}", use_container_width=True):
            st.session_state[sel_key] = None
            st.rerun()

    hand = st.session_state.get(hand_key) or []
    sel = st.session_state.get(sel_key)
    if is_multi:
        sel = sel if isinstance(sel, list) else []
    for r in range(3):
        cols = st.columns(3)
        for c in range(3):
            idx = r * 3 + c
            if idx >= len(hand):
                continue
            with cols[c]:
                _render_pool_card(cat, hand[idx], idx, locked, sel, is_multi, max_sel)


def render_character_build():
    """主角构建页：按世界观生成的候选池 + roll/锁定/选定 + 自定义。"""
    template = st.session_state.get("template")
    if not template:
        st.session_state.character_step = False
        st.rerun()

    # 首次进入：按世界观生成候选池（一次 API 调用）
    if not st.session_state.get("character_pool"):
        with st.spinner("正在按世界观生成候选天赋/体质/金手指…"):
            st.session_state.character_pool = character_builder.generate_pool(st.session_state.llm, template)
    pool = st.session_state.character_pool

    st.markdown(
        '<div class="gm-header"><div class="gm-brand">'
        '<span class="gm-icon">🧑</span>构建主角'
        f'<span class="gm-ver">{VERSION}</span>'
        '</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown("### 构建主角")
    st.caption("roll 候选、锁定心仪的，选定后进入故事。")

    w = template.get("world") or {}
    ctx = w.get("name") or "已选世界"
    if w.get("background"):
        ctx += " · " + w["background"]
    st.caption("世界观：" + ctx)

    if st.button("♻️ 重新生成候选池"):
        st.session_state.character_pool = None
        for k in ("talent_hand", "physique_hand", "gf_hand", "talent_lock", "physique_lock", "gf_lock",
                  "talent_sel", "physique_sel", "gf_sel"):
            st.session_state.pop(k, None)
        st.rerun()

    _ensure_hands(pool)

    tab_t, tab_p, tab_g, tab_c = st.tabs(["🎴 天赋（选1-3）", "🧬 体质（选1）", "✨ 金手指（选1-3）", "✏️ 自定义"])
    with tab_t:
        _render_pool_picker("talent", "天赋", pool["talents"], is_multi=True, max_sel=character_builder.MAX_TALENTS)
    with tab_p:
        _render_pool_picker("physique", "体质", pool["physiques"], is_multi=False, max_sel=1)
    with tab_g:
        _render_pool_picker("gf", "金手指", pool["golden_fingers"], is_multi=True, max_sel=3, allow_none=True)
    with tab_c:
        custom_talent = st.text_input("自定义天赋名", key="custom_talent", placeholder="叠加到已选天赋（可空）")
        custom_physique = st.text_input("自定义体质名", key="custom_physique", placeholder="覆盖上面的体质选择")
        custom_gf = st.text_input("自定义金手指名", key="custom_gf", placeholder="覆盖上面的金手指选择")

    # 汇总
    talents = list(st.session_state.get("talent_sel") or [])
    if (custom_talent or "").strip():
        talents.append(custom_talent.strip())
        talents = talents[: character_builder.MAX_TALENTS]
    physique = (custom_physique or "").strip() or st.session_state.get("physique_sel") or "均衡之躯"

    # 金手指：1~3 个（自定义覆盖）
    gf_list = list(st.session_state.get("gf_sel") or [])
    if (custom_gf or "").strip():
        gf_list = [custom_gf.strip()]
    gf_list = gf_list[:3]

    # 从池子里取等级、描述和代价（用于写入状态）
    all_pool_items = pool["talents"] + pool["physiques"] + pool["golden_fingers"]
    name_to_item = {x["name"]: x for x in all_pool_items}
    tier_map, desc_map, drawback_map = {}, {}, {}
    for n in list(talents) + ([physique] if physique else []) + list(gf_list):
        if n and n in name_to_item:
            tier_map[n] = name_to_item[n]["tier"]
            desc_map[n] = name_to_item[n]["description"]
            drawback_map[n] = name_to_item[n].get("drawback", "")

    st.divider()
    st.markdown("**你的选择**")
    st.info(character_builder.summarize_build(talents, physique, gf_list))
    st.caption("等级颜色：白 < 绿 < 蓝 < 紫 < 金 < 红 < 炫彩；高等级带 ⚠代价；自定义项无固定等级，由主持人按名字发挥。")

    c1, c2, _ = st.columns([1, 1, 2])
    with c1:
        if st.button("确认开始", type="primary", use_container_width=True):
            start_game(character_builder.build_player(
                template, talents, physique, gf_list,
                descriptions=desc_map, tiers=tier_map, drawbacks=drawback_map))
    with c2:
        if st.button("返回世界观", use_container_width=True):
            st.session_state.character_step = False
            st.session_state.pop("template", None)
            st.rerun()


def handle_input(text):
    """先立即展示玩家消息，再在下一轮渲染里显示“思考链”并调用模型。"""
    st.session_state.log.append({"role": "player", "content": text})
    st.session_state.pending_input = text
    st.rerun()


# ---------------------------------------------------------------- 主流程

llm = ensure_llm()

# 无 Key：先输入 Key
if llm is None:
    st.markdown("## 🗡️ 文字冒险 · Game Master")
    st.caption("需要 DeepSeek API Key 才能运行。")
    key = st.text_input("DeepSeek API Key", type="password", placeholder="sk-...")
    if key and key.strip():
        save_api_key(key.strip())
        st.session_state.llm = LLMClient(api_key=key.strip(), model=load_model() or "deepseek-chat", base_url=BASE_URL)
        st.rerun()
    st.stop()

# 侧边栏：存档管理（存档名 + 导出/导入），所有状态都可用
render_save_sidebar()

# 尚未开始游戏 → 主角构建页 / 世界观设置页
if "game" not in st.session_state or st.session_state.game is None:
    if st.session_state.get("character_step"):
        render_character_build()
        st.stop()

    st.markdown(header_html(), unsafe_allow_html=True)
    st.markdown("### 开始新游戏")
    st.caption("选择世界观来源，然后进入故事。")

    col_setup, col_preview = st.columns([1, 1])
    with col_setup:
        mode = st.radio("世界观来源", ["手动输入", "AI 生成", "上传文件"], horizontal=True)
        with st.container(border=True):
            if mode == "手动输入":
                bg = st.text_area("世界观 / 背景", height=90)
                role = st.text_input("你扮演的角色")
                init = st.text_area("初始情境", height=70)
                style = st.text_input("主持风格（可留空）")
                if st.button("开始游戏", type="primary", use_container_width=True):
                    if not bg and not init:
                        st.warning("至少填写世界观/背景或初始情境。")
                    else:
                        start_build({
                            "world": {"name": "", "background": bg, "rules": "", "style": style},
                            "player": {"name": "", "role_description": role, "attributes": {}, "status_effects": []},
                            "initial_situation": init,
                            "location": {"name": "", "description": ""}, "time": "",
                            "characters": {}, "inventory": [], "plot_flags": {}, "current_goal": "",
                        })

            elif mode == "AI 生成":
                theme = st.text_input("主题 / 类型（可留空）", placeholder="修仙 / 赛博朋克 / 悬疑…")
                keywords = st.text_input("关键词 / 要求（可留空）")
                role_hint = st.text_input("想扮演的角色类型（可留空）")
                if st.button("生成世界观", use_container_width=True):
                    with st.spinner("正在生成…"):
                        try:
                            t = world_gen.generate_world(llm, theme, keywords, role_hint)
                        except Exception as e:
                            st.error(str(e))
                            t = None
                    if t:
                        st.session_state.preview = t

            else:  # 上传文件
                up = st.file_uploader("上传 .json / .txt / .md", type=["json", "txt", "md"])
                if up is not None:
                    raw = up.read().decode("utf-8")
                    try:
                        if up.name.lower().endswith(".json"):
                            t = world_gen.load_template_from_json_text(raw)
                        else:
                            with st.spinner("正在将文本结构化为世界观…"):
                                t = world_gen.structure_text(llm, raw, "")
                        st.session_state.preview = t
                    except Exception as e:
                        st.error(str(e))

    with col_preview:
        with st.container(border=True):
            if "preview" in st.session_state:
                st.markdown("**世界观预览**")
                st.markdown(st.session_state.preview.get("world", {}).get("background", "（无背景）"))
                st.markdown("```\n" + world_gen.render_preview(st.session_state.preview) + "\n```")
                if st.button("采用并开始", type="primary", use_container_width=True):
                    start_build(st.session_state.preview)
            else:
                st.caption("生成 / 上传后，这里会显示预览。")

    st.stop()

# ============ 游戏中：三区布局 ============
game = st.session_state.game
log = st.session_state.log
pending = st.session_state.get("pending_input")

# 顶部标题栏
st.markdown(header_html(game.state.data["meta"]["turn"]), unsafe_allow_html=True)

# 待处理输入时，追加一个“思考链”占位气泡（下一轮渲染会先显示它）
show_log = list(log)
if pending:
    show_log.append({"role": "thinking", "content": random_think()})

left, right = st.columns([7, 3], gap="large")

with left:
    # 聊天卡片：消息在上，可选行动与输入框在对话下方，均在框内，内部独立滚动
    with st.container(height=CHAT_HEIGHT, border=True):
        render_log(show_log)

        options = game.state.data.get("options") or []
        if options and not pending:
            st.caption("可选行动")
            for i, o in enumerate(options):
                if st.button(o, key=f"opt_{game.state.data['meta']['turn']}_{i}", use_container_width=True):
                    handle_input(o)

        with st.form("chat_form", clear_on_submit=True):
            user_text = st.text_input("输入行动或对话…", key="chat_input")
            send = st.form_submit_button("发送", use_container_width=True)
        if send and user_text and user_text.strip():
            handle_input(user_text.strip())

with right:
    # 右侧世界状态：独立滚动
    with st.container(height=CHAT_HEIGHT):
        render_world_panel(game)

# 处理待处理输入（此刻页面已渲染出玩家消息 + 思考占位，随后才阻塞调用模型）
if pending:
    st.session_state.pop("pending_input", None)
    out = game.run_turn(pending)
    st.session_state.log.append({"role": "narrator", "content": out})
    st.rerun()
