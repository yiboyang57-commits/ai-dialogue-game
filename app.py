"""AI 对话模拟器 · Streamlit 精致版界面。

运行：streamlit run app.py
复用命令行/图形版的核心逻辑（game.py / world_state.py / judgment.py …），
这里只做界面呈现层：三区布局 + 聊天气泡 + 卡片化世界状态，视觉走简洁白灰 + 单一强调色。
"""
import html

import streamlit as st

from config import BASE_URL, load_api_key, save_api_key, load_model
from game import Game, load_game
from llm_client import LLMClient
from memory import Memory
from world_state import WorldState
import character_builder
import dynamics
import save_bundle
import world_gen

# ---------------------------------------------------------------- 主题与全局样式
# 暖色浅色主题（与桌面版 gui.py 保持一致）：陶土橙 + 暖米色
VERSION = "v2.4"  # 界面版本号：用于确认云端是否已部署最新代码（顶栏可见）
CHAT_HEIGHT = 480  # 聊天区固定高度（像素），内部滚动，输入框/面板保持不动

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
    for m in log:
        if m["role"] == "player":
            st.markdown('<div class="player-label">你</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="player-row"><div class="player-bubble">{esc(m["content"])}</div></div>',
                        unsafe_allow_html=True)
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


def current_save_key():
    """当前玩家的存档名（侧边栏维护），空串=默认槽。"""
    return (st.session_state.get("save_name") or "").strip()


def load_into_session(state_data, memory_dict, save_key=""):
    """把世界状态 + 记忆数据装入当前会话并切到游戏界面。"""
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
    save_key = current_save_key()
    state = WorldState(save_key=save_key)
    state.apply_template(template)
    game = Game(state, Memory(save_key=save_key), st.session_state.llm)
    with st.spinner("主持人构思开场中…"):
        opening = game.start()
    st.session_state.game = game
    st.session_state.log = []
    st.session_state.log.append({"role": "narrator", "content": opening})
    st.session_state.pop("preview", None)
    st.session_state.pop("template", None)
    st.session_state.character_step = False
    st.rerun()


def render_save_sidebar():
    """侧边栏：存档名（多存档隔离）+ 导出/导入存档（跨设备持久化）。"""
    with st.sidebar:
        st.markdown("### 💾 存档")
        if "save_name" not in st.session_state:
            q = st.query_params.get_all("save")
            st.session_state.save_name = (q[-1] if q else "").strip()
        st.text_input("存档名（每人填不同名字）", key="save_name")
        slot = current_save_key()
        st.caption("当前存档槽：" + (slot or "默认") + "（不同名字互相隔离）")

        game = st.session_state.get("game")
        if game is not None:
            data = save_bundle.bundle_json(game.state, game.memory)
            st.download_button(
                "⬇ 导出存档",
                data=data.encode("utf-8"),
                file_name=(slot or "默认") + "_存档.json",
                mime="application/json",
                use_container_width=True,
            )
            if st.button("💾 存到服务器", use_container_width=True):
                game.save()
                st.toast("已存到服务器（临时，长期请用「导出存档」）")

        up = st.file_uploader("⬆ 导入存档", type=["json"], key="import_save")
        if up is not None:
            try:
                state_data, memory_dict = save_bundle.load_bundle(up.getvalue())
            except Exception as e:
                st.error("导入失败：" + str(e))
            else:
                st.session_state.pop("import_save", None)
                load_into_session(state_data, memory_dict, slot)

        st.caption("云端磁盘是临时的，建议「导出」存到自己设备，下次「导入」恢复。")


def start_build(template):
    """世界观已定，进入主角构建步骤。"""
    st.session_state.template = template
    st.session_state.character_step = True
    st.rerun()


def render_character_build():
    """主角构建页：自选天赋 / 体质 / 金手指，确认后开始游戏。"""
    template = st.session_state.get("template")
    if not template:
        st.session_state.character_step = False
        st.rerun()

    st.markdown(
        '<div class="gm-header"><div class="gm-brand">'
        '<span class="gm-icon">🧑</span>构建主角'
        f'<span class="gm-ver">{VERSION}</span>'
        '</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown("### 构建主角")
    st.caption("自选天赋、体质与金手指，然后进入故事。")

    w = template.get("world") or {}
    ctx = w.get("name") or "已选世界"
    if w.get("background"):
        ctx += " · " + w["background"]
    st.caption("世界观：" + ctx)

    col_form, col_preview = st.columns([3, 2])
    with col_form:
        st.markdown("**自选天赋**（可多选，最多 %d 个）" % character_builder.MAX_TALENTS)
        talents = st.multiselect("天赋", character_builder.talent_names())
        if len(talents) > character_builder.MAX_TALENTS:
            st.warning("最多选 %d 个天赋。" % character_builder.MAX_TALENTS)
            talents = talents[: character_builder.MAX_TALENTS]

        st.markdown("**体质**（单选）")
        physique = st.radio("体质", character_builder.physique_names(), index=0)

        st.markdown("**金手指**（单选）")
        gf_label = st.radio("金手指", character_builder.golden_finger_labels(), index=0)

    gf = None if gf_label == "无" else gf_label
    with col_preview:
        st.markdown("**你的选择**")
        st.info(character_builder.summarize_build(talents, physique, gf))

    c1, c2, _ = st.columns([1, 1, 2])
    with c1:
        if st.button("确认开始", type="primary", use_container_width=True):
            start_game(character_builder.build_player(template, talents, physique, gf))
    with c2:
        if st.button("返回世界观", use_container_width=True):
            st.session_state.character_step = False
            st.session_state.pop("template", None)
            st.rerun()


def handle_input(text):
    game = st.session_state.game
    st.session_state.log.append({"role": "player", "content": text})
    with st.spinner("主持人思考中…"):
        out = game.run_turn(text)
    st.session_state.log.append({"role": "narrator", "content": out})
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

    # 读档（按当前存档名隔离）
    save_key = current_save_key()
    if WorldState.load(save_key) is not None:
        st.divider()
        label = "继续存档" + ("（" + save_key + "）" if save_key else "")
        if st.button(label):
            game = load_game(llm, save_key)
            st.session_state.game = game
            st.session_state.log = []
            st.session_state.log.append({"role": "narrator", "content": "（已读取存档）\n" + game.state.to_player_summary()})
            st.rerun()

    st.stop()

# ============ 游戏中：三区布局 ============
game = st.session_state.game
log = st.session_state.log

# 顶部标题栏
st.markdown(header_html(game.state.data["meta"]["turn"]), unsafe_allow_html=True)

# 主体两栏
left, right = st.columns([7, 3], gap="large")

with left:
    # 聊天区：固定高度、内部滚动
    with st.container(height=CHAT_HEIGHT):
        render_log(log)

    # 可选行动（显而易见选项，点击即发送）—— 固定在滚动区下方，始终可见
    options = game.state.data.get("options") or []
    if options:
        st.markdown('<div class="gm-card-title" style="margin-top:10px;">可选行动</div>', unsafe_allow_html=True)
        cols = st.columns(len(options))
        for i, o in enumerate(options):
            if cols[i].button(o, use_container_width=True):
                handle_input(o)

with right:
    render_world_panel(game)

# 底部固定输入框（聊天式）
user = st.chat_input("输入行动或对话…")
if user:
    handle_input(user.strip())
