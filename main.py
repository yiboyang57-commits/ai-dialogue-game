"""命令行入口：AI 对话模拟器（Game Master 原型）。

用法：
    python3 main.py
首次运行会引导输入 DeepSeek API Key（也可用环境变量 DEEPSEEK_API_KEY）。

开始新游戏前，可选择世界观来源：
  1) AI 自定义生成世界观
  2) 上传自己的世界观文件（JSON / txt / md）
  3) 手动逐项输入
"""
import json
import sys

from config import BASE_URL, DEFAULT_MODEL, load_api_key, load_model, save_api_key
from game import Game, load_game
from llm_client import LLMClient, LLMError
from memory import Memory
from world_state import WorldState
import character_builder
import gm_rules
import world_gen


def banner():
    print("=" * 60)
    print("  AI 对话模拟器 · Game Master 原型（DeepSeek）")
    print("=" * 60)


def print_help():
    print("\n可用命令：")
    print("  /state   查看当前世界状态摘要")
    print("  /rules   查看主持人限制条件")
    print("  /save    立即保存")
    print("  /quit    保存并退出")
    print("  /help    显示本帮助")
    print("  输入数字可选用下方选项，或直接输入自定义行动/对话。")


def print_options(options):
    if not options:
        return
    print("\n可选行动：")
    for i, o in enumerate(options, 1):
        print(f"  {i}) {o}")
    print("  （也可直接输入自定义行动）")


def ensure_api_key():
    key = load_api_key()
    if key:
        return key
    print("未找到 DeepSeek API Key（可通过环境变量 DEEPSEEK_API_KEY 提供）。")
    print("请前往 https://platform.deepseek.com 获取。")
    key = input("请输入 DeepSeek API Key (sk-...)：").strip()
    if not key:
        print("未提供 Key，退出。")
        sys.exit(1)
    save_api_key(key)
    print("已保存到 config.json。")
    return key


# ---------------------------------------------------------------- 世界观来源

def choose_world_template(llm):
    """开始游戏前，让用户选择世界观来源，返回标准模板 dict。"""
    print("\n—— 开始新游戏：选择世界观来源 ——")
    print("  [1] AI 自定义生成世界观（按主题/关键词生成）")
    print("  [2] 上传自己的世界观文件（JSON / txt / md）")
    print("  [3] 手动逐项输入")
    while True:
        choice = input("请选择 [1/2/3]：").strip()
        if choice == "1":
            t = generate_world_flow(llm)
            if t is not None:
                return t
        elif choice == "2":
            t = upload_world_flow(llm)
            if t is not None:
                return t
        elif choice == "3":
            return manual_template()
        else:
            print("输入无效，请输入 1、2 或 3。")


def _ask_adopt():
    c = input("\n采用这个世界观？[Y=采用 / r=重新生成 / n=返回]：").strip().lower()
    return c


def generate_world_flow(llm):
    theme = input("主题/类型（例：科幻 / 奇幻 / 武侠 / 都市 / 悬疑 / 末世，留空=AI 自选）：").strip()
    keywords = input("补充关键词或要求（留空跳过）：").strip()
    role_hint = input("你希望扮演的角色类型（留空=AI 自定）：").strip()

    while True:
        print("\n（正在生成世界观…）")
        try:
            template = world_gen.generate_world(llm, theme, keywords, role_hint)
        except LLMError as e:
            print(f"\n[错误] {e}")
            return None

        print("\n" + "-" * 60)
        print(world_gen.render_preview(template))
        print("-" * 60)
        c = _ask_adopt()
        if c in ("", "y", "yes", "是"):
            return template
        if c in ("r", "regen", "重新生成"):
            continue
        return None


def upload_world_flow(llm):
    path = input("世界观文件路径（JSON 或 .txt/.md）：").strip()
    if not path:
        print("未输入路径。")
        return None
    try:
        loaded = world_gen.load_template_from_file(path)
    except FileNotFoundError as e:
        print(f"\n[错误] {e}")
        return None
    except (ValueError, json.JSONDecodeError) as e:
        print(f"\n[错误] 文件解析失败：{e}")
        return None

    # 文本文件 → 交给 AI 结构化；JSON → 直接用
    if isinstance(loaded, dict) and "__raw_text__" in loaded:
        role_hint = input("你希望扮演的角色类型（留空=AI 自定）：").strip()
        print("\n（正在将设定文本结构化为世界观…）")
        try:
            template = world_gen.structure_text(llm, loaded["__raw_text__"], role_hint)
        except LLMError as e:
            print(f"\n[错误] {e}")
            return None
    else:
        template = loaded

    print("\n" + "-" * 60)
    print(world_gen.render_preview(template))
    print("-" * 60)
    if _ask_adopt() in ("", "y", "yes", "是"):
        return template
    return None


def manual_template():
    print("\n—— 手动输入世界观 ——")
    world_bg = input("1/4 世界观 / 世界背景（例：一座终年被雾笼罩的蒸汽都市…）：").strip()
    role = input("2/4 你扮演的角色（例：一名调查连环失踪案的私家侦探）：").strip()
    initial = input("3/4 初始情境（例：深夜，你的事务所门被敲响…）：").strip()
    style = input("4/4 主持风格 / 叙事语气（例：冷峻的侦探小说笔法；留空=由 AI 按世界观自动匹配）：").strip()
    return {
        "world": {"name": "", "background": world_bg, "rules": "", "style": style},
        "player": {"name": "", "role_description": role, "attributes": {}, "status_effects": []},
        "initial_situation": initial,
        "location": {"name": "", "description": ""},
        "time": "",
        "characters": {},
        "inventory": [],
        "plot_flags": {},
        "current_goal": "",
    }


# ---------------------------------------------------------------- 主角构建

def _pick_multi(prompt, options, max_n=None, min_n=0):
    """命令行多选：返回选中的项名列表（按序号，逗号分隔）。"""
    print(prompt)
    for i, o in enumerate(options, 1):
        print(f"  {i}) {o}")
    raw = input("编号（逗号分隔，直接回车=跳过）：").strip()
    if not raw:
        return []
    out = []
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            idx = int(part) - 1
        except ValueError:
            continue
        if 0 <= idx < len(options):
            out.append(options[idx])
    if max_n is not None and len(out) > max_n:
        print(f"最多选 {max_n} 个，已只取前 {max_n} 个。")
        out = out[:max_n]
    if len(out) < min_n:
        out = []
    return out


def _pick_single(prompt, options, default_idx=0):
    """命令行单选：返回选中的项名。"""
    print(prompt)
    for i, o in enumerate(options, 1):
        print(f"  {i}) {o}")
    raw = input(f"编号（默认 {default_idx + 1}）：").strip()
    if not raw:
        return options[default_idx]
    try:
        idx = int(raw) - 1
    except ValueError:
        idx = default_idx
    if not (0 <= idx < len(options)):
        idx = default_idx
    return options[idx]


def build_character(template):
    """开始新游戏前构建主角：自选天赋 / 体质 / 金手指，返回合并后的模板。"""
    print("\n" + "=" * 60)
    print("—— 构建主角 ——")
    print("=" * 60)

    talent_names_list = _pick_multi(
        "\n【自选天赋】可多选，最多 %d 个（序号用逗号分隔，如 1,3,5）：" % character_builder.MAX_TALENTS,
        character_builder.talent_names(),
        max_n=character_builder.MAX_TALENTS,
    )

    physique_name = _pick_single("\n【体质】选择一项（默认 1）：", character_builder.physique_names(), default_idx=0)

    gf_labels = character_builder.golden_finger_labels()
    gf_label = _pick_single("\n【金手指】是否自带外挂（默认 1=无）：", gf_labels, default_idx=0)
    golden_finger_name = None if gf_label == "无" else gf_label

    print("\n" + "-" * 60)
    print("你的主角构建：")
    print(character_builder.summarize_build(talent_names_list, physique_name, golden_finger_name))
    print("-" * 60)

    while True:
        c = input("\n采用这个构建？[Y=采用 / r=重新构建 / n=返回世界观]：").strip().lower()
        if c in ("", "y", "yes", "是"):
            return character_builder.build_player(template, talent_names_list, physique_name, golden_finger_name)
        if c in ("r", "regen", "重新构建"):
            return build_character(template)
        return None


# ---------------------------------------------------------------- 开局 / 续玩

def start_new_game(llm):
    while True:
        template = choose_world_template(llm)
        if template is None:
            print("\n未选择世界观，退出。")
            sys.exit(0)
        template = build_character(template)
        if template is not None:
            break
        # template 为 None：返回世界观选择
        print("\n返回世界观选择…\n")

    state = WorldState()
    state.apply_template(template)
    memory = Memory()
    game = Game(state, memory, llm)

    print("\n（主持人思考中…）")
    try:
        opening = game.start()
    except LLMError as e:
        print(f"\n[错误] {e}")
        sys.exit(1)
    print("\n主持人：" + (opening or "（无开场文本）"))
    print_options(game.state.data.get("options") or [])
    return game


def continue_game(llm):
    game = load_game(llm)
    print("\n已读取存档，当前进度摘要：")
    print("-" * 60)
    print(game.state.to_player_summary())
    print("-" * 60)
    return game


def loop(game):
    while True:
        try:
            user = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            game.save()
            print("\n已保存，再见！")
            break

        if not user:
            continue

        if user.startswith("/"):
            cmd = user[1:].strip().lower()
            if cmd in ("quit", "exit", "q"):
                game.save()
                print("已保存，再见！")
                break
            elif cmd == "state":
                print("\n" + game.state.to_player_summary())
                continue
            elif cmd == "rules":
                print("\n当前主持人限制条件（编辑 gm_rules.json 可增删）：")
                for i, r in enumerate(game.rules, 1):
                    print(f"  {i}. {r}")
                continue
            elif cmd == "save":
                game.save()
                print("已保存。")
                continue
            elif cmd == "help":
                print_help()
                continue
            else:
                print("未知命令，输入 /help 查看。")
                continue

        # 数字选择：把「1/2/3…」映射到上一步给出的显而易见选项
        opts = game.state.data.get("options") or []
        if opts and user.isdigit():
            idx = int(user) - 1
            if 0 <= idx < len(opts):
                user = opts[idx]

        print("\n（主持人思考中…）")
        try:
            text = game.run_turn(user)
        except LLMError as e:
            print(f"\n[错误] {e}")
            print("本轮未保存，可重新输入，或 /quit 退出。")
            continue

        print("\n主持人：" + (text or "（本轮无叙事文本）"))
        print_options(game.state.data.get("options") or [])


def main():
    banner()
    gm_rules.ensure_rules_file()
    key = ensure_api_key()
    model = load_model() or DEFAULT_MODEL
    llm = LLMClient(api_key=key, model=model, base_url=BASE_URL)

    if WorldState.load() is not None:
        choice = input("\n检测到已有存档，是否继续上次游戏？[Y/n]：").strip().lower()
        if choice in ("", "y", "yes", "是"):
            game = continue_game(llm)
        else:
            print("\n将覆盖旧存档，开始新游戏。")
            game = start_new_game(llm)
    else:
        game = start_new_game(llm)

    print_help()
    loop(game)


if __name__ == "__main__":
    main()
