"""可视化 GUI（tkinter，零额外依赖）· 暖色浅色 + 圆角卡片风。

复用命令行版的核心逻辑（game.py / world_state.py / judgment.py …），
这里只做界面呈现层：顶部圆角工具栏 + 左侧聊天气泡 + 右侧世界状态面板 + 底部可选行动/输入框。

运行：python3 gui.py
打包：见 game.spec 与 README 的「打包为可执行文件」章节。
"""
import json
import os
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import font as tkfont
from tkinter import ttk

from config import BASE_URL, load_api_key, save_api_key, load_model
from game import Game
from llm_client import LLMClient
from memory import Memory
from world_state import WorldState
import character_builder
import dynamics
import world_gen

# ---------------------------------------------------------------- 配色（暖色浅色）
BG          = "#f2ebe0"   # 暖米色背景（不白、偏暖）
CARD        = "#fffbf3"   # 暖象牙卡片
CARD_2      = "#f6ecdd"   # 次级 / 输入 / 按钮底
CARD_3      = "#ecdfc9"   # 悬浮
BORDER      = "#e8dcc7"   # 暖边框
TEXT        = "#4a4136"   # 暖棕文字
MUTED       = "#9a8e7d"   # 暖灰
FAINT       = "#b8ad9c"   # 更淡的暖灰
ACCENT      = "#d67c4e"   # 陶土橙（主强调）
ACCENT_DK   = "#c0693f"   # 强调色·深（悬浮）
ACCENT_SOFT = "#f7e3d3"   # 玩家气泡（暖桃）
NARR_BUBBLE = "#f0e7d6"   # 旁白气泡（暖沙）
PLAYER_FG   = "#5b3a27"   # 玩家气泡文字
GOOD        = "#5f9e6b"
WARN        = "#cf9a3f"
BAD         = "#c4563c"

TITLE = "文字冒险 · Game Master"
RADIUS = 16               # 卡片圆角半径
CARD_PAD = 9              # 卡片内容内边距


# ---------------------------------------------------------------- 圆角绘制
def _rr(canvas, x1, y1, x2, y2, r, **kw):
    """在 canvas 上画一个圆角矩形（smooth 多边形）。"""
    r = max(1, min(int(r), (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y1 + r, x2, y2 - r, x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2,
        x1 + r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y2 - r, x1, y1 + r,
        x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


class Card(tk.Canvas):
    """圆角卡片容器：自绘圆角底 + 1px 暖边框，内容放进 self.inner。"""

    def __init__(self, master, fill=CARD, radius=RADIUS, pad=CARD_PAD, **kw):
        bg = kw.pop("bg", None) or master.cget("bg")
        tk.Canvas.__init__(self, master, bg=bg, highlightthickness=0, bd=0, **kw)
        self._fill = fill
        self._radius = radius
        self._pad = pad
        self.inner = tk.Frame(self, bg=fill)
        self._win = self.create_window(pad, pad, window=self.inner, anchor="nw", tags=("win",))
        self.bind("<Configure>", self._resize)

    def _resize(self, e):
        w, h = e.width, e.height
        self.delete("shape")
        if w > 2 and h > 2:
            _rr(self, 0, 0, w, h, self._radius, fill=BORDER, outline="", tags=("shape",))
            _rr(self, 1, 1, w - 1, h - 1, self._radius, fill=self._fill, outline="", tags=("shape",))
        self.coords(self._win, self._pad, self._pad)
        self.itemconfigure(self._win, width=max(1, w - 2 * self._pad),
                           height=max(1, h - 2 * self._pad))


class RoundedButton(tk.Canvas):
    """圆角按钮：自绘圆角底 + 居中文字，支持悬浮/按下/禁用。"""

    def __init__(self, master, text, command=None, fill=CARD_2, fg=TEXT,
                 hover_fill=None, font=None, padx=16, height=34, radius=12):
        bg = master.cget("bg")
        tk.Canvas.__init__(self, master, bg=bg, highlightthickness=0, bd=0,
                           cursor="hand2", height=height)
        self._text = text
        self._command = command
        self._fill = fill
        self._fg = fg
        self._hover_fill = hover_fill or fill
        self._radius = radius
        self._font = font
        self._enabled = True
        self._hovered = False
        self._pressed = False
        f = tkfont.Font(font=font) if font else tkfont.nametofont("TkDefaultFont")
        self.configure(width=f.measure(text) + 2 * padx)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _on_enter(self, e):
        self._hovered = True
        self._draw()

    def _on_leave(self, e):
        self._hovered = False
        self._pressed = False
        self._draw()

    def _on_press(self, e):
        if self._enabled:
            self._pressed = True
            self._draw()

    def _on_release(self, e):
        was = self._pressed
        self._pressed = False
        self._draw()
        if was and self._enabled and self._command:
            self._command()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        if not self._enabled:
            fill, fg = self._fill, FAINT
        elif self._pressed:
            fill, fg = self._hover_fill, self._fg
        elif self._hovered:
            fill, fg = self._hover_fill, self._fg
        else:
            fill, fg = self._fill, self._fg
        _rr(self, 0, 0, w - 1, h - 1, self._radius, fill=fill, outline="")
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)

    def set_enabled(self, enabled):
        self._enabled = enabled
        self._hovered = False
        self._pressed = False
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()


# ---------------------------------------------------------------- 小工具
def _affinity_label(info, turn):
    aff = info.get("affinity")
    if not isinstance(aff, (int, float)):
        return None
    eff = dynamics.effective_affinity(info, turn)
    if eff >= 70:
        return "好感 高"
    if eff >= 40:
        return "好感 中"
    if eff >= 15:
        return "好感 低"
    return "好感 陌生"


def _hp_band(d):
    attrs = d.get("player", {}).get("attributes") or {}
    hp = attrs.get("hp")
    maxhp = attrs.get("max_hp") or 100
    if not isinstance(hp, (int, float)):
        return None
    ratio = hp / max(maxhp, 1)
    if ratio <= 0:
        return "濒死"
    if ratio < 0.25:
        return "重伤"
    if ratio < 0.6:
        return "受伤"
    if ratio < 0.9:
        return "轻伤"
    return "健康"


def _truncate(text, limit=14):
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ApiKeyDialog(tk.Toplevel):
    """暖色 API Key 输入框。"""

    def __init__(self, master):
        super().__init__(master)
        self.result = None
        self.title("DeepSeek API Key")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(master)

        tk.Label(self, text="请输入 DeepSeek API Key", bg=BG, fg=TEXT, font=(FONT[0], 13, "bold")).pack(
            padx=28, pady=(22, 6), anchor="w")
        tk.Label(self, text="首次运行需要，之后会保存在本程序旁。", bg=BG, fg=MUTED, font=FONT_SMALL).pack(
            padx=28, pady=(0, 10), anchor="w")

        self.entry = ttk.Entry(self, show="*", width=44)
        self.entry.pack(padx=28, pady=4, fill="x")
        self.entry.focus_set()

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=28, pady=(16, 20))
        RoundedButton(btns, "取消", command=self.destroy, fill=CARD_2, hover_fill=CARD_3,
                      font=FONT_SMALL, padx=14, height=32).pack(side="right")
        RoundedButton(btns, "确定", command=self._ok, fill=ACCENT, fg="#ffffff",
                      hover_fill=ACCENT_DK, font=FONT_SMALL, padx=18, height=32).pack(side="right", padx=(0, 10))

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self._center(master)

    def _ok(self):
        self.result = (self.entry.get() or "").strip()
        self.destroy()

    def _center(self, master):
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")


class SetupDialog(tk.Toplevel):
    """新游戏世界观来源对话框（暖色圆角），返回标准模板 dict（或 None）。"""

    def __init__(self, master, llm):
        super().__init__(master)
        self.llm = llm
        self.result = None
        self.template = None
        self.q = queue.Queue()
        self.title("新游戏 · 选择世界观来源")
        self.configure(bg=BG)
        self.transient(master)
        self.grab_set()
        self._build()
        self._center(master)
        self.after(100, self._poll)

    def _build(self):
        self.mode = tk.StringVar(value="manual")

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(head, text="开始新游戏", bg=BG, fg=TEXT, font=(FONT[0], 16, "bold")).pack(side="left")
        tk.Label(head, text="选择世界观来源，然后进入故事", bg=BG, fg=MUTED, font=FONT_SMALL).pack(
            side="left", padx=(10, 0), pady=(4, 0))

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=18, pady=4)
        for text, val in (("手动输入", "manual"), ("AI 生成", "ai"), ("上传 JSON/文本", "upload")):
            ttk.Radiobutton(row, text=text, variable=self.mode, value=val,
                            style="TRadiobutton", command=self._switch).pack(side="left", padx=(0, 14))

        self.manual_frame = tk.Frame(self, bg=BG)
        self.ai_frame = tk.Frame(self, bg=BG)
        self.upload_frame = tk.Frame(self, bg=BG)

        self._labeled(self.manual_frame, "世界观 / 背景", "m_bg", 0)
        self._labeled(self.manual_frame, "你扮演的角色", "m_role", 1)
        self._labeled(self.manual_frame, "初始情境", "m_init", 2)
        self._labeled(self.manual_frame, "主持风格（可留空）", "m_style", 3)

        self._labeled(self.ai_frame, "主题 / 类型（可留空）", "ai_theme", 0)
        self._labeled(self.ai_frame, "关键词 / 要求（可留空）", "ai_keywords", 1)
        self._labeled(self.ai_frame, "想扮演的角色类型（可留空）", "ai_role", 2)

        self.up_path = tk.StringVar()
        f = self.upload_frame
        tk.Label(f, text="文件路径", bg=BG, fg=MUTED, font=FONT_SMALL).grid(row=0, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(f, textvariable=self.up_path, width=40).grid(row=0, column=1, padx=4)
        RoundedButton(f, "浏览", command=self._browse, font=FONT_SMALL, height=30).grid(row=0, column=2, padx=4)

        tk.Label(f, text="或直接粘贴文本（粘了就用文本，否则用上面文件）", bg=BG, fg=MUTED, font=FONT_SMALL).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=6, pady=(8, 3))
        self.paste_text = tk.Text(f, height=8, wrap="word", bg=CARD, fg=TEXT, insertbackground=TEXT,
                                  relief="flat", borderwidth=0, highlightthickness=1,
                                  highlightbackground=BORDER, highlightcolor=ACCENT,
                                  padx=10, pady=8, font=FONT)
        self.paste_text.grid(row=2, column=0, columnspan=3, sticky="ew", padx=6, pady=(0, 4))

        tk.Label(self, text="预览", bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w").pack(fill="x", padx=18, pady=(10, 2))
        preview_card = Card(self, height=320)
        preview_card.pack(fill="both", expand=True, padx=18, pady=4)
        self.preview = tk.Text(preview_card.inner, wrap="word", bg=CARD, fg=TEXT, insertbackground=TEXT,
                               relief="flat", borderwidth=0, highlightthickness=0,
                               padx=12, pady=10, font=FONT, state="disabled")
        self.preview.pack(fill="both", expand=True)

        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=18, pady=(8, 16))
        self.act_btn = RoundedButton(btns, "生成 / 读取", command=self._on_action, font=FONT_SMALL)
        self.act_btn.pack(side="left", padx=(0, 8))
        RoundedButton(btns, "采用并开始", command=self._on_use, fill=ACCENT, fg="#ffffff",
                      hover_fill=ACCENT_DK, font=FONT_SMALL).pack(side="left")
        RoundedButton(btns, "取消", command=self.destroy, font=FONT_SMALL).pack(side="right")

        self._switch()

    def _center(self, master):
        self.update_idletasks()
        w, h = 780, 760
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")

    def _labeled(self, parent, label, var_name, row):
        v = tk.StringVar()
        setattr(self, var_name, v)
        tk.Label(parent, text=label, bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w").grid(
            row=row, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(parent, textvariable=v, width=52).grid(row=row, column=1, padx=4, pady=3)

    def _switch(self):
        for fr in (self.manual_frame, self.ai_frame, self.upload_frame):
            fr.pack_forget()
        mode = self.mode.get()
        if mode == "manual":
            self.manual_frame.pack(fill="x", padx=12, pady=6)
            self.act_btn.set_enabled(False)
        elif mode == "ai":
            self.ai_frame.pack(fill="x", padx=12, pady=6)
            self.act_btn.set_enabled(True)
        else:
            self.upload_frame.pack(fill="x", padx=12, pady=6)
            self.act_btn.set_enabled(True)

    def _browse(self):
        path = filedialog.askopenfilename(
            title="选择世界观文件",
            filetypes=[("世界观文件", "*.json *.txt *.md"), ("所有文件", "*.*")])
        if path:
            self.up_path.set(path)

    def _set_preview(self, text):
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def _on_action(self):
        mode = self.mode.get()
        if mode == "ai":
            self._gen_ai()
        elif mode == "upload":
            self._load_upload()

    def _on_use(self):
        mode = self.mode.get()
        if mode == "manual":
            t = self._manual_template()
            if t:
                self.result = t
                self.destroy()
        else:
            if self.template:
                self.result = self.template
                self.destroy()
            else:
                messagebox.showwarning("提示", "请先「生成 / 读取」世界观，再采用。")

    def _manual_template(self):
        bg = self.m_bg.get().strip()
        role = self.m_role.get().strip()
        init = self.m_init.get().strip()
        if not bg and not init:
            messagebox.showwarning("提示", "至少填写世界观/背景或初始情境。")
            return None
        return {
            "world": {"name": "", "background": bg, "rules": "", "style": self.m_style.get().strip()},
            "player": {"name": "", "role_description": role, "attributes": {}, "status_effects": []},
            "initial_situation": init,
            "location": {"name": "", "description": ""},
            "time": "",
            "characters": {}, "inventory": [], "plot_flags": {}, "current_goal": "",
        }

    def _gen_ai(self):
        self._set_preview("（正在生成世界观…）")
        theme = self.ai_theme.get().strip()
        keywords = self.ai_keywords.get().strip()
        role = self.ai_role.get().strip()
        self._async(lambda: world_gen.generate_world(self.llm, theme, keywords, role), self._on_gen_done)

    def _load_upload(self):
        pasted = self.paste_text.get("1.0", "end").strip()
        if pasted:
            loaded = self._parse_pasted(pasted)
            self._after_load(loaded)
            return

        path = self.up_path.get().strip()
        if not path:
            messagebox.showwarning("提示", "请粘贴文本，或填写/选择文件路径。")
            return
        try:
            loaded = world_gen.load_template_from_file(path)
        except Exception as e:
            messagebox.showerror("读取失败", str(e))
            return
        self._after_load(loaded)

    @staticmethod
    def _parse_pasted(text):
        """粘贴的内容：若能解析成 JSON 对象则直接当模板，否则当普通文本交给 AI 结构化。"""
        stripped = text.strip()
        if stripped[:1] in ("{", "["):
            try:
                data = json.loads(stripped)
                if isinstance(data, dict):
                    return world_gen.normalize_template(data)
            except (ValueError, json.JSONDecodeError):
                pass
        return {"__raw_text__": stripped}

    def _after_load(self, loaded):
        if isinstance(loaded, dict) and "__raw_text__" in loaded:
            self._set_preview("（正在将文本结构化为世界观…）")
            self._async(lambda: world_gen.structure_text(self.llm, loaded["__raw_text__"], ""), self._on_gen_done)
        else:
            self._on_gen_done(loaded)

    def _on_gen_done(self, template):
        self.template = template
        self._set_preview(world_gen.render_preview(template))

    def _async(self, fn, cb):
        def worker():
            try:
                r = fn()
            except Exception as e:
                self.q.put((cb, None, str(e)))
            else:
                self.q.put((cb, r, None))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                cb, r, err = self.q.get_nowait()
                if err:
                    self._set_preview(f"[错误] {err}")
                else:
                    cb(r)
        except queue.Empty:
            pass
        self.after(100, self._poll)


class CharacterDialog(tk.Toplevel):
    """新游戏主角构建对话框：自选 / 随机 / 自定义 天赋 · 体质 · 金手指。"""

    def __init__(self, master, template):
        super().__init__(master)
        self.template = template
        self.confirmed = False
        self.back_to_world = False
        self.result_template = None
        self.title("新游戏 · 构建主角")
        self.configure(bg=BG)
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)
        self._build()
        self._center(master)

    def _build(self):
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(head, text="构建主角", bg=BG, fg=TEXT, font=(FONT[0], 16, "bold")).pack(side="left")
        tk.Label(head, text="可自选、随机，或直接输入自定义", bg=BG, fg=MUTED, font=FONT_SMALL).pack(
            side="left", padx=(10, 0), pady=(4, 0))
        RoundedButton(head, "🎲 随机", command=self._on_roll, fill=CARD_2, hover_fill=CARD_3,
                      font=FONT_SMALL, padx=14, height=30).pack(side="right")

        # 世界观上下文提示
        w = self.template.get("world") or {}
        ctx = w.get("name") or "已选世界"
        if w.get("background"):
            ctx += " · " + _truncate(w["background"], 36)
        tk.Label(self, text="世界观：" + ctx, bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w").pack(
            fill="x", padx=18, pady=(0, 8))

        # —— 天赋（多选，0~3 个）——
        tk.Label(self, text="自选天赋（可多选，最多 3 个）", bg=BG, fg=TEXT, font=FONT_LABEL, anchor="w").pack(
            fill="x", padx=18, pady=(4, 2))
        talent_frame = tk.Frame(self, bg=BG)
        talent_frame.pack(fill="x", padx=18, pady=2)
        self.talent_vars = {}
        self.talent_desc = {t["name"]: t["description"] for t in character_builder.TALENTS}
        for i, t in enumerate(character_builder.TALENTS):
            var = tk.IntVar(value=0)
            self.talent_vars[t["name"]] = var
            tk.Checkbutton(talent_frame, text=t["name"], variable=var, bg=BG, fg=TEXT,
                           activebackground=BG, selectcolor=CARD, font=FONT_SMALL,
                           command=self._refresh_preview, anchor="w").grid(
                row=i // 3, column=i % 3, sticky="w", padx=2, pady=2)
        for col in range(3):
            talent_frame.columnconfigure(col, weight=1)
        self.custom_talent = self._custom_row("自定义天赋（顿号/逗号分隔多个）")

        # —— 体质（单选）——
        tk.Label(self, text="体质（单选）", bg=BG, fg=TEXT, font=FONT_LABEL, anchor="w").pack(
            fill="x", padx=18, pady=(10, 2))
        physique_frame = tk.Frame(self, bg=BG)
        physique_frame.pack(fill="x", padx=18, pady=2)
        self.physique_var = tk.StringVar(value=character_builder.PHYSIQUES[0]["name"])
        self.physique_desc = {p["name"]: p["description"] for p in character_builder.PHYSIQUES}
        for i, p in enumerate(character_builder.PHYSIQUES):
            ttk.Radiobutton(physique_frame, text=p["name"], variable=self.physique_var, value=p["name"],
                            style="TRadiobutton", command=self._refresh_preview).grid(
                row=i // 3, column=i % 3, sticky="w", padx=2, pady=2)
        for col in range(3):
            physique_frame.columnconfigure(col, weight=1)
        self.custom_physique = self._custom_row("或自定义体质名")

        # —— 金手指（单选）——
        tk.Label(self, text="金手指（单选）", bg=BG, fg=TEXT, font=FONT_LABEL, anchor="w").pack(
            fill="x", padx=18, pady=(10, 2))
        gf_frame = tk.Frame(self, bg=BG)
        gf_frame.pack(fill="x", padx=18, pady=2)
        self.gf_var = tk.StringVar(value=character_builder.golden_finger_labels()[0])
        self.gf_desc = {g["name"] or "无": g["description"] for g in character_builder.GOLDEN_FINGERS}
        for i, g in enumerate(character_builder.GOLDEN_FINGERS):
            label = g["name"] or "无"
            ttk.Radiobutton(gf_frame, text=label, variable=self.gf_var, value=label,
                            style="TRadiobutton", command=self._refresh_preview).grid(
                row=i // 3, column=i % 3, sticky="w", padx=2, pady=2)
        for col in range(3):
            gf_frame.columnconfigure(col, weight=1)
        self.custom_gf = self._custom_row("或自定义金手指名")

        # —— 预览 ——
        tk.Label(self, text="你的选择", bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w").pack(
            fill="x", padx=18, pady=(10, 2))
        preview_card = Card(self, height=80)
        preview_card.pack(fill="x", padx=18, pady=4)
        self.preview = tk.Text(preview_card.inner, wrap="word", bg=CARD, fg=TEXT, relief="flat",
                               borderwidth=0, highlightthickness=0, padx=10, pady=8, font=FONT_SMALL,
                               state="disabled")
        self.preview.pack(fill="both", expand=True)

        # —— 按钮 ——
        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=18, pady=(8, 16))
        RoundedButton(btns, "确认开始", command=self._on_confirm, fill=ACCENT, fg="#ffffff",
                      hover_fill=ACCENT_DK, font=FONT_SMALL, padx=18, height=34).pack(side="left")
        RoundedButton(btns, "取消", command=self.destroy, font=FONT_SMALL, padx=14, height=34).pack(side="right")
        RoundedButton(btns, "返回世界观", command=self._on_back, font=FONT_SMALL, padx=14, height=34).pack(
            side="right", padx=(0, 8))

        self._refresh_preview()

    def _custom_row(self, label):
        """一行「自定义」输入框，返回其 StringVar。"""
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=18, pady=(4, 0))
        tk.Label(row, text=label, bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w").pack(side="left")
        var = tk.StringVar()
        ttk.Entry(row, textvariable=var, width=36).pack(side="left", padx=(8, 0), fill="x", expand=True)
        var.trace_add("write", lambda *a: self._refresh_preview())
        return var

    @staticmethod
    def _parse_custom(text):
        names = [s.strip() for s in (text or "").replace("，", ",").replace("、", ",").split(",")]
        return [n for n in names if n]

    def _selected_talents(self):
        out = [name for name, var in self.talent_vars.items() if var.get()]
        for n in self._parse_custom(self.custom_talent.get()):
            if n not in out:
                out.append(n)
        return out[:character_builder.MAX_TALENTS]

    def _selected_physique(self):
        return (self.custom_physique.get() or "").strip() or self.physique_var.get()

    def _selected_gf(self):
        label = (self.custom_gf.get() or "").strip() or self.gf_var.get()
        return None if label in ("无", "") else label

    def _on_roll(self):
        t, p, g = character_builder.roll_build()
        for name, var in self.talent_vars.items():
            var.set(1 if name in t else 0)
        self.physique_var.set(p)
        self.gf_var.set(g or "无")
        self.custom_talent.set("")
        self.custom_physique.set("")
        self.custom_gf.set("")
        self._refresh_preview()

    def _refresh_preview(self):
        talents = self._selected_talents()
        lines = []
        if talents:
            lines.append("天赋：" + "、".join(f"{n}（{self.talent_desc.get(n, '自定义')}）" for n in talents))
        else:
            lines.append("天赋：无")
        pname = self._selected_physique()
        lines.append("体质：" + f"{pname}（{self.physique_desc.get(pname, '自定义')}）")
        gname = self._selected_gf() or "无"
        lines.append("金手指：" + f"{gname}（{self.gf_desc.get(gname, '自定义')}）")
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", "\n".join(lines))
        self.preview.configure(state="disabled")

    def _on_confirm(self):
        talents = self._selected_talents()
        if len(talents) > character_builder.MAX_TALENTS:
            messagebox.showwarning("提示", f"天赋最多选 {character_builder.MAX_TALENTS} 个。")
            return
        physique = self._selected_physique()
        gf = self._selected_gf()
        self.result_template = character_builder.build_player(self.template, talents, physique, gf)
        self.confirmed = True
        self.destroy()

    def _on_back(self):
        self.back_to_world = True
        self.destroy()

    def _center(self, master):
        self.update_idletasks()
        w, h = 800, 720
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")


class GameApp:
    """主窗口：暖色浅色 + 圆角卡片。"""

    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.llm = None
        self.game = None
        self.busy = False
        self._dots = 0
        self._dot_job = None

        global FONT, FONT_SMALL, FONT_LABEL, FONT_TITLE
        FONT, FONT_SMALL, FONT_LABEL, FONT_TITLE = _pick_fonts(root)

        root.title(TITLE)
        root.configure(bg=BG)
        root.minsize(960, 620)
        self._build_style()
        self._build_ui()
        self._init_llm()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll)

    def _init_llm(self):
        key = load_api_key()
        if not key:
            dlg = ApiKeyDialog(self.root)
            self.root.wait_window(dlg)
            if dlg.result:
                key = dlg.result
                save_api_key(key)
        if not key:
            messagebox.showerror("缺少 API Key", "未提供 API Key，无法运行。")
            self.root.destroy()
            return
        self.llm = LLMClient(api_key=key, model=load_model() or "deepseek-chat", base_url=BASE_URL)

    def _build_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TEntry", fieldbackground=CARD_2, foreground=TEXT, bordercolor=BORDER,
                        insertcolor=TEXT, padding=8, font=FONT)
        style.map("TEntry", bordercolor=[("focus", ACCENT)])
        style.configure("TRadiobutton", background=BG, foreground=TEXT, font=FONT_SMALL,
                        focuscolor=BG, padding=(2, 4))
        style.map("TRadiobutton", background=[("active", BG)], foreground=[("active", ACCENT_DK)])
        style.configure("Vertical.TScrollbar", background=CARD_3, troughcolor=BG,
                        bordercolor=BG, arrowcolor=MUTED, relief="flat")
        style.map("Vertical.TScrollbar", background=[("active", BORDER)])

    def _build_ui(self):
        # 顶部工具栏（圆角卡片）
        header = Card(self.root, height=58)
        header.pack(fill="x", padx=12, pady=(12, 6))
        tk.Label(header.inner, text="◆", bg=CARD, fg=ACCENT, font=(FONT[0], 14, "bold")).pack(side="left", padx=(8, 6))
        tk.Label(header.inner, text=TITLE, bg=CARD, fg=TEXT, font=(FONT[0], 14, "bold")).pack(side="left")
        self.turn_label = tk.Label(header.inner, text="", bg=CARD, fg=MUTED, font=FONT_SMALL)
        self.turn_label.pack(side="left", padx=(14, 0))

        RoundedButton(header.inner, "保存", command=self._save, font=FONT_SMALL, height=30).pack(side="right", padx=(4, 6))
        RoundedButton(header.inner, "规则", command=self._show_rules, font=FONT_SMALL, height=30).pack(side="right", padx=4)
        RoundedButton(header.inner, "状态", command=self._toggle_panel, font=FONT_SMALL, height=30).pack(side="right", padx=4)
        RoundedButton(header.inner, "继续上次", command=self._continue, font=FONT_SMALL, height=30).pack(side="right", padx=4)
        RoundedButton(header.inner, "新游戏", command=self._new_game, fill=ACCENT, fg="#ffffff",
                      hover_fill=ACCENT_DK, font=FONT_SMALL, height=30).pack(side="right", padx=4)

        # 主体：左聊天 + 右面板（圆角卡片）
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=6)

        self.side = Card(body, width=304)
        self.side.pack(side="right", fill="y")
        self.side.inner.rowconfigure(0, weight=1)
        self.side.inner.columnconfigure(0, weight=1)
        self.panel = tk.Text(self.side.inner, wrap="word", bg=CARD, fg=TEXT, relief="flat",
                             borderwidth=0, highlightthickness=0, padx=12, pady=10,
                             font=FONT_SMALL, state="disabled", cursor="arrow")
        self.panel.grid(row=0, column=0, sticky="nsew")
        psb = ttk.Scrollbar(self.side.inner, orient="vertical", command=self.panel.yview, style="Vertical.TScrollbar")
        psb.grid(row=0, column=1, sticky="ns")
        self.panel.configure(yscrollcommand=psb.set)
        self._configure_panel_tags()

        chat = Card(body)
        chat.pack(side="left", fill="both", expand=True, padx=(0, 10))
        chat.inner.rowconfigure(0, weight=1)
        chat.inner.columnconfigure(0, weight=1)
        self.log = tk.Text(chat.inner, wrap="word", bg=CARD, fg=TEXT, insertbackground=TEXT,
                           relief="flat", borderwidth=0, highlightthickness=0,
                           padx=12, pady=10, font=FONT, state="disabled", cursor="arrow")
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(chat.inner, orient="vertical", command=self.log.yview, style="Vertical.TScrollbar")
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)
        self._configure_tags()

        # 可选行动（圆角 pill）
        self.opts_frame = tk.Frame(self.root, bg=BG)
        self.opts_frame.pack(fill="x", padx=12, pady=(0, 4))

        # 输入（圆角卡片）
        input_card = Card(self.root, height=56)
        input_card.pack(fill="x", padx=12, pady=(4, 8))
        self.entry = ttk.Entry(input_card.inner, font=FONT)
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=1)
        self.entry.bind("<Return>", lambda e: self._send())
        self.send_btn = RoundedButton(input_card.inner, "发送", command=self._send, fill=ACCENT, fg="#ffffff",
                                      hover_fill=ACCENT_DK, font=FONT_SMALL, padx=20, height=34)
        self.send_btn.pack(side="right")

        self.status_label = tk.Label(self.root, text="", bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w")
        self.status_label.pack(fill="x", padx=16, pady=(0, 4))

        self._append_system("欢迎来到文字冒险。点击「新游戏」开始，或「继续上次」读档。")
        self._set_busy(False)

    def _configure_tags(self):
        c = self.log
        c.tag_configure("player_label", foreground=ACCENT_DK, font=FONT_LABEL, justify="right",
                        spacing1=14, spacing3=3, rmargin=24)
        c.tag_configure("player_bubble", background=ACCENT_SOFT, foreground=PLAYER_FG, font=FONT,
                        justify="left", lmargin1=220, lmargin2=220, rmargin=24,
                        spacing1=12, spacing3=14)
        c.tag_configure("narrator_label", foreground=MUTED, font=FONT_LABEL, justify="left",
                        spacing1=14, spacing3=3, lmargin1=24)
        c.tag_configure("narrator_bubble", background=NARR_BUBBLE, foreground=TEXT, font=FONT,
                        justify="left", lmargin1=24, lmargin2=24, rmargin=280,
                        spacing1=12, spacing3=14)
        c.tag_configure("sys", foreground=FAINT, font=FONT_SMALL, justify="center",
                        spacing1=10, spacing3=10)
        c.tag_configure("err", foreground=BAD, font=FONT_SMALL, justify="center",
                        spacing1=10, spacing3=10)
        c.tag_configure("gap", spacing1=6, spacing3=6)

    def _configure_panel_tags(self):
        p = self.panel
        p.tag_configure("p_title", foreground=TEXT, font=(FONT[0], 15, "bold"), spacing1=4, spacing3=10)
        p.tag_configure("p_head", foreground=ACCENT_DK, font=(FONT[0], 12, "bold"), spacing1=12, spacing3=5)
        p.tag_configure("p_body", foreground=TEXT, font=FONT_SMALL, spacing1=1, spacing3=1)
        p.tag_configure("p_muted", foreground=MUTED, font=FONT_SMALL, spacing1=1, spacing3=1)
        p.tag_configure("p_faint", foreground=FAINT, font=FONT_SMALL, spacing1=1, spacing3=1)
        p.tag_configure("p_avatar", background=ACCENT, foreground="#ffffff", font=(FONT[0], 11, "bold"),
                        spacing1=2, spacing3=2)

    # ---------- 消息写入 ----------
    def _append_text(self, blocks):
        self.log.configure(state="normal")
        for text, tag in blocks:
            self.log.insert("end", text, tag)
        self.log.configure(state="disabled")
        self.log.see("end")

    def _append_player(self, text):
        paras = [p for p in re.split(r"\n{2,}", (text or "").strip()) if p.strip()] or [text or ""]
        blocks = [("\n你\n", "player_label")]
        for i, para in enumerate(paras):
            if i:
                blocks.append(("\n", "gap"))
            blocks.append((para + "\n", "player_bubble"))
        self._append_text(blocks)

    def _append_narrator(self, text):
        paras = [p for p in re.split(r"\n{2,}", (text or "").strip()) if p.strip()]
        if not paras:
            paras = ["（本轮无叙事文本）"]
        blocks = [("\n旁白\n", "narrator_label")]
        for i, para in enumerate(paras):
            if i:
                blocks.append(("\n", "gap"))
            blocks.append((para + "\n", "narrator_bubble"))
        self._append_text(blocks)

    def _append_system(self, text):
        self._append_text([("\n" + text + "\n", "sys")])

    def _append_error(self, text):
        self._append_text([("\n⚠ " + text + "\n", "err")])

    # ---------- 忙碌状态 ----------
    def _set_busy(self, busy):
        self.busy = busy
        if busy:
            self.entry.configure(state="disabled")
            self.send_btn.set_enabled(False)
            self._clear_options()
            self._start_dots()
        else:
            self.entry.configure(state="normal")
            self.send_btn.set_enabled(True)
            self._stop_dots()
            self.entry.focus_set()

    def _start_dots(self):
        self._dots = 0
        self._tick_dots()

    def _tick_dots(self):
        if not self.busy:
            return
        self._dots = (self._dots % 3) + 1
        self.status_label.configure(text="主持人思考中" + "·" * self._dots)
        self._dot_job = self.root.after(400, self._tick_dots)

    def _stop_dots(self):
        if self._dot_job:
            self.root.after_cancel(self._dot_job)
            self._dot_job = None
        self.status_label.configure(text="准备就绪")

    # ---------- 可选行动 ----------
    def _clear_options(self):
        for w in self.opts_frame.winfo_children():
            w.destroy()

    def _set_options(self, options):
        self._clear_options()
        if not options:
            return
        tk.Label(self.opts_frame, text="可选行动", bg=BG, fg=MUTED, font=FONT_LABEL).pack(
            side="top", anchor="w", padx=6, pady=(0, 6))
        grid = tk.Frame(self.opts_frame, bg=BG)
        grid.pack(fill="x")
        for i in range(2):
            grid.columnconfigure(i, weight=1)
        for i, o in enumerate(options):
            full = o
            btn = RoundedButton(grid, _truncate(o), command=lambda t=full: self._send(t),
                                fill=CARD_2, fg=TEXT, hover_fill=ACCENT_SOFT,
                                font=FONT_SMALL, padx=14, height=32, radius=16)
            btn.grid(row=i // 2, column=i % 2, sticky="w", padx=3, pady=3)

    # ---------- 右侧面板 ----------
    def _refresh_panel(self):
        if self.game is None:
            self._render_panel_blocks([("尚未开始游戏。\n", "p_muted")])
            return
        d = self.game.state.data
        turn = d.get("meta", {}).get("turn", 0)
        loc = d.get("location", {}) or {}
        self.turn_label.configure(text=f"第 {turn} 回合")

        blocks = [("世界状态\n", "p_title")]

        top = []
        if loc.get("name"):
            top.append("地点：" + str(loc["name"]))
        if d.get("time"):
            top.append("时间：" + str(d["time"]))
        if top:
            blocks.append(("　·　".join(top) + "\n", "p_muted"))

        chars = d.get("characters") or {}
        blocks.append(("角色\n", "p_head"))
        if chars:
            for name, info in chars.items():
                avatar = (name[0] if name else "?")
                subs = []
                if info.get("description"):
                    subs.append(str(info["description"]))
                aff = _affinity_label(info, turn)
                if aff:
                    subs.append(aff)
                if info.get("status"):
                    subs.append(str(info["status"]))
                sub = " · ".join(subs) if subs else ""
                blocks.append((f" {avatar} ", "p_avatar"))
                blocks.append((f"  {name}\n", "p_body"))
                if sub:
                    blocks.append(("　　" + sub + "\n", "p_muted"))
                if info.get("location"):
                    blocks.append(("　　地点：" + str(info["location"]) + "\n", "p_faint"))
        else:
            blocks.append(("暂无角色\n", "p_muted"))

        inv = d.get("inventory") or []
        blocks.append(("物品\n", "p_head"))
        if inv:
            items = "　".join(f"{i.get('name')} ×{i.get('quantity', 1)}" for i in inv[:15])
            blocks.append((items + "\n", "p_body"))
        else:
            blocks.append(("空\n", "p_muted"))

        goal = d.get("current_goal") or ""
        blocks.append(("当前目标\n", "p_head"))
        blocks.append(((goal if goal else "—") + "\n", "p_body" if goal else "p_muted"))

        flags = d.get("plot_flags") or {}
        if flags:
            blocks.append(("剧情标记\n", "p_head"))
            blocks.append(("、".join(f"{k}" for k in list(flags.keys())[:20]) + "\n", "p_body"))

        player = d.get("player", {}) or {}
        status = player.get("status_effects") or []
        band = _hp_band(d)
        if band:
            status = ([band] + status) if band not in status else status
        talents = [t.get("name") if isinstance(t, dict) else str(t) for t in (player.get("talents") or [])]
        talents = [t for t in talents if t]
        blocks.append(("状态\n", "p_head"))
        blocks.append(("　·　".join(status) + "\n", "p_body" if status else "p_muted"))
        blocks.append(("天赋：" + ("、".join(talents) if talents else "无") + "\n", "p_muted"))
        physique = player.get("physique") or {}
        gfs = player.get("golden_fingers") or []
        gf_names = [g.get("name") for g in gfs if isinstance(g, dict) and g.get("name")]
        if physique.get("name") or gf_names:
            blocks.append(("体质：" + (physique.get("name") or "无") + "　金手指：" + ("、".join(gf_names) if gf_names else "无") + "\n", "p_muted"))

        self._render_panel_blocks(blocks)

    def _render_panel_blocks(self, blocks):
        self.panel.configure(state="normal")
        self.panel.delete("1.0", "end")
        for text, tag in blocks:
            self.panel.insert("end", text, tag)
        self.panel.configure(state="disabled")

    def _toggle_panel(self):
        if self.side.winfo_manager():
            self.side.pack_forget()
        else:
            self.side.pack(side="right", fill="y")

    # ---------- 游戏流程 ----------
    def _new_game(self):
        if self.busy:
            return
        if self.game is not None and not messagebox.askyesno("确认", "将开始新游戏（当前进度会自动覆盖存档），继续？"):
            return
        template = None
        while True:
            dlg = SetupDialog(self.root, self.llm)
            self.root.wait_window(dlg)
            if not dlg.result:
                return  # 取消世界观选择
            cdlg = CharacterDialog(self.root, dlg.result)
            self.root.wait_window(cdlg)
            if cdlg.confirmed:
                template = cdlg.result_template
                break
            if cdlg.back_to_world:
                continue  # 返回世界观选择，重新来
            return  # 取消构建
        state = WorldState()
        state.apply_template(template)
        self.game = Game(state, Memory(), self.llm)
        self._append_system("（" + world_gen.world_loading_phrase(template) + "）")
        self._refresh_panel()
        self._set_busy(True)
        self._async(self.game.start, self._on_opening)

    def _continue(self):
        if self.busy:
            return
        if WorldState.load() is None:
            messagebox.showinfo("提示", "没有找到存档。")
            return
        self.game = Game(WorldState.load(), Memory.load(), self.llm)
        self._append_system("已读取存档。")
        self._refresh_panel()
        self._set_options(self.game.state.data.get("options") or [])

    def _on_opening(self, opening):
        self._set_busy(False)
        self._append_narrator(opening or "（无开场文本）")
        self._refresh_panel()
        self._set_options(self.game.state.data.get("options") or [])

    def _send(self, text=None):
        if self.busy:
            return
        if text is None:
            text = self.entry.get().strip()
            self.entry.delete(0, "end")
        if not text:
            return
        if self.game is None:
            messagebox.showinfo("提示", "请先「新游戏」或「继续上次」。")
            return
        self._append_player(text)
        self._clear_options()
        self._set_busy(True)
        self._async(lambda: self.game.run_turn(text), self._on_turn)

    def _on_turn(self, out):
        self._set_busy(False)
        self._append_narrator(out or "（本轮无叙事文本）")
        self._refresh_panel()
        self._set_options(self.game.state.data.get("options") or [])

    # ---------- 其它 ----------
    def _show_rules(self):
        if self.game is None:
            messagebox.showinfo("提示", "尚未开始游戏。")
            return
        rules = "\n".join(f"{i}. {r}" for i, r in enumerate(self.game.rules, 1))
        top = tk.Toplevel(self.root)
        top.title("主持人限制条件")
        top.configure(bg=BG)
        top.geometry("560x480")
        top.transient(self.root)
        tk.Label(top, text="主持人限制条件", bg=BG, fg=TEXT, font=(FONT[0], 14, "bold")).pack(padx=16, pady=(14, 4), anchor="w")
        card = Card(top)
        card.pack(fill="both", expand=True, padx=16, pady=8)
        box = tk.Text(card.inner, wrap="word", bg=CARD, fg=TEXT, font=FONT, relief="flat", padx=12, pady=10)
        box.pack(fill="both", expand=True)
        box.insert("1.0", rules)
        box.configure(state="disabled")
        RoundedButton(top, "关闭", command=top.destroy, fill=ACCENT, fg="#ffffff",
                      hover_fill=ACCENT_DK, font=FONT_SMALL).pack(pady=(0, 14))

    def _save(self):
        if self.game is None:
            return
        self.game.save()
        self._append_system("（已保存）")

    def _on_close(self):
        if self.game is not None:
            try:
                self.game.save()
            except Exception:
                pass
        self.root.destroy()

    # ---------- 异步 ----------
    def _async(self, fn, cb):
        def worker():
            try:
                r = fn()
            except Exception as e:
                self.q.put((cb, None, str(e)))
            else:
                self.q.put((cb, r, None))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                cb, r, err = self.q.get_nowait()
                self._set_busy(False)
                if err:
                    self._append_error(err)
                    self._refresh_panel()
                else:
                    cb(r)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)


# ---------------------------------------------------------------- 字体选择
FONT = ("TkDefaultFont", 14)
FONT_SMALL = ("TkDefaultFont", 11)
FONT_LABEL = ("TkDefaultFont", 11)
FONT_TITLE = ("TkDefaultFont", 16)


def _pick_fonts(root):
    families = set(tkfont.families(root))
    prefs = ["PingFang SC", "Hiragino Sans GB", "Helvetica Neue", "Microsoft YaHei UI", "Microsoft YaHei", "Arial"]
    family = next((f for f in prefs if f in families), "TkDefaultFont")
    return (family, 14), (family, 11), (family, 11), (family, 16)


def main():
    try:
        root = tk.Tk()
    except tk.TclError as e:
        print("无法启动图形界面（可能没有可用的显示环境）：", e)
        print("请在有图形界面的系统上运行，或改用命令行版：python3 main.py")
        return
    GameApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
