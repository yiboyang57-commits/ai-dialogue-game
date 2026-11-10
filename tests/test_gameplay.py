"""游戏结果与状态必须一致；这些回归检查不调用API、不使用玩家存档。"""
import copy
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import combat
import judgment
import memory
import prompts
import seasons
import world_state
from game import Game
from llm_client import LLMError


class Replies:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.messages = []
        self.summary_error = False

    def generate(self, messages):
        self.messages.append(copy.deepcopy(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return copy.deepcopy(reply)

    def summarize(self, messages):
        if self.summary_error:
            raise LLMError("模拟压缩失败")
        return "【本章摘要】\n本章\n【全局摘要】\n全局"


class GameFixture(unittest.TestCase):
    """共用夹具：隔离存档目录，并提供带初始状态的 Game。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wanjie-regression-")
        self.addCleanup(self.tmp.cleanup)
        for module in (memory, world_state):
            patcher = patch.object(module, "SAVE_DIR", self.tmp.name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def game(self, llm, hp=100):
        state = world_state.WorldState()
        state.apply_template({
            "world": {"background": "普通修仙世界", "rules": "无复活能力"},
            "player": {"name": "林舟", "attributes": {"hp": hp, "max_hp": 100}},
            "location": {"name": "山崖"}, "time": "第一日上午",
            "inventory": [{"name": "护身符", "quantity": 1}],
        })
        return Game(state, memory.Memory(), llm, rules=[])


class GameplayTests(GameFixture):
    def test_only_final_result_and_options_are_published(self):
        llm = Replies(
            ("错误草稿：敌人已经死了", {"combat": {"enemy_name": "地痞", "enemy_power": 15, "action": "fight"},
                                        "inventory_add": [{"name": "战利品"}], "options": ["领取胜利奖励"]}),
            ("最终结果：地痞挡开了你的缴械尝试。", {"options": ["后退"], "turn_event": "缴械失败"}),
        )
        game = self.game(llm)
        with patch.object(random, "random", return_value=0.999999):
            text = game.run_turn("只打落地痞的武器，不杀人；结束后停在原地")
        self.assertEqual(text, "最终结果：地痞挡开了你的缴械尝试。")
        self.assertEqual(game.state.data["options"], ["后退"])
        self.assertNotIn("战利品", [i["name"] for i in game.state.data["inventory"]])
        context = "\n".join(m["content"] for m in llm.messages[1])
        self.assertIn("只打落地痞的武器，不杀人", context)
        self.assertIn("地痞", context)
        self.assertEqual(game.memory.recent[-1]["content"], text)

    def test_lethal_combat_ends_life_and_blocks_resurrection(self):
        llm = Replies(
            ("准备冲突", {"combat": {"enemy_name": "强敌", "enemy_power": 30, "action": "fight"}}),
            ("玩家死亡。", {"player_attributes": {"hp": 999}, "player_status_effects": ["健康"],
                          "attribute_events": [{"attribute": "hp", "delta": 1000}], "options": ["继续战斗"]}),
        )
        game = self.game(llm, hp=5)
        with patch.object(random, "random", return_value=0.999999):
            game.run_turn("迎战强敌")
        self.assertTrue(game.is_over)
        self.assertEqual(game.state.data["player"]["attributes"]["hp"], 0)
        self.assertEqual(game.state.data["player"]["status_effects"], ["死亡"])
        self.assertEqual(game.state.data["options"], [])
        turn = game.state.data["meta"]["turn"]
        game.run_turn("奇迹般复活继续攻击")
        self.assertEqual(len(llm.messages), 2)
        self.assertEqual(game.state.data["meta"]["turn"], turn)
        self.assertTrue(world_state.WorldState.load().is_dead)

    def test_explicit_nonlethal_sparring_can_end_in_unconsciousness(self):
        llm = Replies(
            ("切磋", {"combat": {"enemy_name": "教习", "enemy_power": 30, "action": "fight", "lethal": False}}),
            ("你在切磋中昏迷。", {"options": []}),
        )
        game = self.game(llm, hp=5)
        with patch.object(random, "random", return_value=0.999999):
            game.run_turn("参加明确不会杀人的练习")
        self.assertFalse(game.is_over)
        self.assertEqual(game.state.data["player"]["attributes"]["hp"], 1)
        self.assertIn("昏迷", game.state.data["player"]["status_effects"])

    def test_inevitable_fatal_action_has_no_roll_or_pity_escape(self):
        llm = Replies(
            ("跳下悬崖", {"judgment": {"type": "action", "subject": "落向崖底尖石", "certain_failure": True,
                                              "failure_consequence": "death", "reason": "不会飞行，千丈崖下无缓冲和救援"}}),
            ("玩家坠崖死亡。", {"player_attributes": {"hp": 50}, "options": ["在崖底寻找机缘"]}),
        )
        game = self.game(llm)
        game.state.data["pity"]["action"] = {"fails": 99}
        game.state.data["player"]["talents"] = [{"name": "天命之子", "tier": "炫彩"}]
        with patch.object(random, "random", return_value=0):
            game.run_turn("没有保护地跳入千丈深渊")
        self.assertTrue(game.is_over)
        self.assertEqual(game.state.data["player"]["death_cause"], "不会飞行，千丈崖下无缓冲和救援")

    def test_fatal_risk_has_no_guaranteed_success_but_ordinary_pity_remains(self):
        ordinary = {"fails": 99}
        dangerous = {"fails": 99}
        with patch.object(random, "random", return_value=0.999999):
            success, _ = judgment.resolve_judgment("action", {}, [], 0, ordinary)
            fatal_success, _ = judgment.resolve_judgment("action", {}, [], 0, dangerous, allow_pity=False)
        self.assertTrue(success)
        self.assertFalse(fatal_success)

    def test_injury_can_exhaust_life(self):
        llm = Replies(
            ("危险尝试", {"judgment": {"type": "action", "failure_consequence": "injury", "failure_damage": 40}}),
            ("伤势导致死亡。", {"options": []}),
        )
        game = self.game(llm, hp=10)
        with patch.object(random, "random", return_value=0.999999):
            game.run_turn("冒险尝试")
        self.assertTrue(game.is_over)

    def test_failed_second_call_rolls_back_items_hp_memory_and_disk(self):
        llm = Replies(
            ("准备逃跑", {"location": "新地点", "combat": {"enemy_name": "敌人", "enemy_power": 20,
                                                            "action": "flee", "use_item": "护身符"}}),
            LLMError("模拟网络失败"),
        )
        game = self.game(llm)
        game.save()
        before = copy.deepcopy(game.state.data)
        memory_before = copy.deepcopy(game.memory.to_dict())
        disk_before = {p.name: p.read_bytes() for p in Path(self.tmp.name).glob("*.json")}
        with self.assertRaises(LLMError):
            game.run_turn("消耗护身符逃跑")
        self.assertEqual(game.state.data, before)
        self.assertEqual(game.memory.to_dict(), memory_before)
        self.assertEqual({p.name: p.read_bytes() for p in Path(self.tmp.name).glob("*.json")}, disk_before)

    def test_failed_judgment_rolls_back_pity(self):
        llm = Replies(("尝试", {"judgment": {"type": "action"}}), LLMError("结果调用失败"))
        game = self.game(llm)
        with self.assertRaises(LLMError):
            game.run_turn("尝试潜入")
        self.assertEqual(game.state.data["pity"], {})

    def test_missing_tool_update_does_not_save_unreliable_narrative(self):
        game = self.game(Replies(("获胜了", None), ("还是没有工具", None)))
        with self.assertRaises(LLMError):
            game.run_turn("攻击")
        self.assertEqual(game.state.data["meta"]["turn"], 0)
        self.assertEqual(game.memory.recent, [])

    def test_compression_failure_preserves_completed_turn_and_buffer(self):
        llm = Replies(("本轮结束。", {"turn_event": "本轮完成"}), ("下一轮。", {}))
        llm.summary_error = True
        game = self.game(llm)
        game.memory.turns_since_compression = 19
        game.run_turn("观察四周")
        self.assertEqual(game.state.data["meta"]["turn"], 1)
        self.assertEqual(game.memory.turns_since_compression, 20)
        self.assertEqual(len(game.memory.buffer), 2)
        self.assertEqual(json.loads(Path(game.state.state_path).read_text())["meta"]["turn"], 1)
        llm.summary_error = False
        game.run_turn("继续观察")
        self.assertEqual(game.memory.turns_since_compression, 0)
        self.assertEqual(game.memory.buffer, [])

    def test_legacy_zero_hp_save_is_terminal(self):
        game = self.game(Replies(), hp=0)
        game.state.data["player"].pop("life_status", None)
        self.assertTrue(game.is_over)
        self.assertEqual(game.state._hp_band(), "死亡")

    def test_code_owned_death_survives_a_missing_final_tool_call(self):
        llm = Replies(("致命尝试", {"judgment": {"type": "action", "certain_failure": True, "failure_consequence": "death"}}),
                      ("林舟在此身死。", None))
        game = self.game(llm)
        text = game.run_turn("没有保护地坠落")
        self.assertEqual(text, "林舟在此身死。")
        self.assertTrue(game.is_over)
        self.assertTrue(world_state.WorldState.load().is_dead)

    def test_nonexistent_item_does_not_grant_guaranteed_escape(self):
        llm = Replies(("逃跑", {"combat": {"enemy_name": "敌人", "enemy_power": 30, "action": "flee", "use_item": "护身符"}}),
                      ("未能逃脱。", {"options": []}))
        game = self.game(llm)
        game.state.data["inventory"] = []
        with patch.object(random, "random", return_value=0.999999):
            game.run_turn("用不存在的护身符逃跑")
        self.assertEqual(game.state.data["last_combat"]["outcome"], "flee_fail")
        self.assertIn("没有该道具", llm.messages[1][-1]["content"])

    def test_death_is_reported_even_on_victory_or_failed_escape(self):
        for action, value in (("fight", 0), ("flee", 0.999999)):
            with self.subTest(action=action), patch.object(random, "random", return_value=value):
                result = combat.resolve(10, 10, 5, action, combat.curve_params("realm_gap"), 1)
                self.assertTrue(result["player_down"])
                self.assertIn("已死亡", combat.gm_brief(result, 1))

    def test_existing_jade_protection_is_spent_once_and_persists(self):
        combat_request = {"combat": {"enemy_name": "强敌", "enemy_power": 30, "action": "fight"}}
        llm = Replies(("第一击", combat_request), ("古玉抵挡了致命一击，你重伤。", {}),
                      ("第二击", combat_request), ("第二次致命攻击导致死亡。", {}))
        game = self.game(llm, hp=5)
        game.state.data["player"]["talents"] = [{"name": "古玉护佑", "description": "抵挡致命一击"}]
        with patch.object(random, "random", return_value=0.999999):
            game.run_turn("迎战强敌")
            self.assertFalse(game.is_over)
            self.assertEqual(game.state.data["player"]["attributes"]["hp"], 1)
            self.assertEqual(world_state.WorldState.load().data["player"]["death_protections_used"], {"古玉护佑": 1})
            game.run_turn("继续迎战")
        self.assertTrue(game.is_over)
        self.assertEqual(game.state.data["player"]["death_protections_used"], {"古玉护佑": 1})

    def test_failed_outcome_does_not_spend_jade_protection(self):
        llm = Replies(("战斗", {"combat": {"enemy_name": "强敌", "enemy_power": 30, "action": "fight"}}), LLMError("网络失败"))
        game = self.game(llm, hp=5)
        game.state.data["player"]["talents"] = ["古玉护佑"]
        with self.assertRaises(LLMError), patch.object(random, "random", return_value=0.999999):
            game.run_turn("迎战强敌")
        self.assertNotIn("death_protections_used", game.state.data["player"])
        self.assertEqual(game.state.data["player"]["attributes"]["hp"], 5)


class RobustnessTests(unittest.TestCase):
    """v2.18 审查修复的回归：血量边界、因素正负号、网络异常、格式容错、存档安全。"""

    # 复用 GameplayTests 的临时存档目录与开局，但不重复跑它的用例
    setUp = GameplayTests.setUp
    game = GameplayTests.game

    # ---- 1. 普通回合不能扣血或致死，只能回血到上限 ----
    def test_normal_turn_cannot_kill_or_damage(self):
        llm = Replies(
            ("一阵风吹过。", {"player_attributes": {"hp": 0}}),
            ("你吃了个果子。", {"attribute_events": [{"attribute": "hp", "delta": -500}]}),
        )
        game = self.game(llm, hp=60)
        game.run_turn("散步")
        game.run_turn("吃果子")
        self.assertFalse(game.is_over)
        self.assertEqual(game.state.data["player"]["attributes"]["hp"], 60)

    def test_normal_turn_heal_is_capped_at_max_hp(self):
        llm = Replies(
            ("你调息片刻。", {"attribute_events": [{"attribute": "hp", "delta": 10}]}),
            ("你服下仙丹。", {"player_attributes": {"hp": 99999}}),
        )
        game = self.game(llm, hp=60)
        game.run_turn("调息")
        self.assertEqual(game.state.data["player"]["attributes"]["hp"], 70)
        game.run_turn("服丹")
        self.assertEqual(game.state.data["player"]["attributes"]["hp"], 100)

    def test_invalid_max_hp_is_ignored(self):
        llm = Replies(("……", {"player_attributes": {"max_hp": 0}}))
        game = self.game(llm)
        game.run_turn("发呆")
        self.assertEqual(game.state.data["player"]["attributes"]["max_hp"], 100)

    # ---- 2. 因素一律“正=有利”：越危险成功率越低 ----
    def test_all_factor_weights_follow_positive_is_favourable(self):
        for jtype, spec in judgment.JUDGMENT_TYPES.items():
            for name, w in spec["factors"].items():
                self.assertGreater(w, 0, f"{jtype}.{name} 的权重必须为正")

    def test_risky_action_is_less_likely(self):
        for name in ("风险可控度", "风险程度"):
            danger = judgment.compute_judgment_probability("action", {name: -3}, [], 0, 0)
            safe = judgment.compute_judgment_probability("action", {name: 3}, [], 0, 0)
            self.assertLess(danger, safe, name)
        alert = judgment.compute_judgment_probability("sneak", {"对方警觉度": -3}, [], 0, 0)
        lax = judgment.compute_judgment_probability("sneak", {"对方警觉度": 3}, [], 0, 0)
        self.assertLess(alert, lax)

    def test_factor_names_match_without_slash(self):
        self.assertEqual(judgment.factor_weight("treasure", "福缘运气"), 1.0)
        self.assertEqual(judgment.factor_weight("relationship", "相处时长 / 见面次数"), 0.6)

    # ---- 3. 压缩阶段任何异常都不丢掉已生成的回合；网络中断转为 LLMError ----
    def test_compression_timeout_keeps_turn(self):
        llm = Replies(("第二十轮叙事", {"turn_event": "继续前行"}))
        llm.summarize = lambda messages: (_ for _ in ()).throw(TimeoutError("read timed out"))
        game = self.game(llm)
        game.memory.turns_since_compression = 19
        self.assertEqual(game.run_turn("赶路"), "第二十轮叙事")
        self.assertEqual(game.state.data["meta"]["turn"], 1)
        self.assertTrue(game.memory.needs_compression())  # 下轮再试

    def test_network_interruptions_become_llm_error(self):
        import llm_client
        client = llm_client.LLMClient(api_key="x")
        for exc in (TimeoutError("timed out"), ConnectionResetError("reset")):
            with patch.object(llm_client.urllib.request, "urlopen", side_effect=exc):
                with self.assertRaises(LLMError):
                    client.summarize([{"role": "user", "content": "hi"}])

    # ---- 4. 模型输出格式偏差不让整轮报错 ----
    def test_malformed_update_fields_do_not_crash(self):
        llm = Replies(
            ("你突破了。", {"player_attributes": {"修为": "筑基初期"}}),
            ("你继续修炼。", {"attribute_events": [{"event": "修为精进"}, {"attribute": "声望", "delta": "五"}],
                           "inventory_add": ["灵石", {"name": "丹药", "quantity": "2"}, 42],
                           "player_status_effects": "中毒",
                           "characters": ["不是对象"], "plot_flags": "x", "options": "继续修炼"}),
            ("你出手。", {"combat": "和山贼打一架", "judgment": ["坏格式"]}),
        )
        game = self.game(llm)
        game.run_turn("突破")
        game.run_turn("修炼")
        d = game.state.data
        self.assertEqual(d["player"]["attributes"]["修为"], "筑基初期")
        self.assertEqual(d["player"]["status_effects"], ["中毒"])
        self.assertEqual({i["name"]: i["quantity"] for i in d["inventory"]}, {"护身符": 1, "灵石": 1, "丹药": 2})
        self.assertEqual(d["options"], ["继续修炼"])
        game.run_turn("出手")
        self.assertEqual(d["meta"]["turn"], 3)

    # ---- 5. 存档原子写入；损坏的存档给出明确错误 ----
    def test_save_is_atomic_and_corrupt_save_is_reported(self):
        import config
        game = self.game(Replies(("开场", {"turn_event": "开始"})))
        game.state.save_key = game.memory.save_key = "甲"
        game.run_turn("开始")
        path = Path(game.state.state_path)
        self.assertTrue(path.exists())
        self.assertFalse([p for p in Path(self.tmp.name).iterdir() if p.name.endswith(".tmp")])
        path.write_text('{"meta": {"turn": 3', encoding="utf-8")
        with self.assertRaises(config.SaveCorruptError):
            world_state.WorldState.load("甲")
        Path(game.memory.history_path).write_text("{坏", encoding="utf-8")
        self.assertEqual(memory.Memory.load("甲").recent, [])

    # ---- 6. 手写世界观 JSON 的常见格式 ----
    def test_handwritten_template_formats(self):
        import world_gen
        t = world_gen.load_template_from_json_text(json.dumps({
            "world": {"background": "修仙界"},
            "inventory": ["铁剑", {"name": "干粮", "quantity": 3}],
            "characters": [{"name": "师父", "description": "严厉"}, "师妹"],
        }, ensure_ascii=False))
        self.assertEqual(t["inventory"][0], {"name": "铁剑", "quantity": 1, "description": ""})
        self.assertEqual(t["characters"]["师父"], {"description": "严厉"})
        self.assertIn("师妹", t["characters"])
        world_gen.render_preview(t)
        state = world_state.WorldState()
        state.apply_template(t)
        state.to_summary()
        t2 = world_gen.load_template_from_json_text(json.dumps({
            "world": {"background": "修仙界"}, "inventory": {"灵石": 50}, "characters": {"掌柜": "笑眯眯的"},
        }, ensure_ascii=False))
        self.assertEqual(t2["inventory"], [{"name": "灵石", "quantity": 50, "description": ""}])
        self.assertEqual(t2["characters"]["掌柜"], {"description": "笑眯眯的"})


class GameplayTuningTests(unittest.TestCase):
    """v2.19 玩法调整：成人向尺度、去掉能力代价、天赋软上限、NPC 记忆、初始好感。"""

    setUp = GameplayTests.setUp
    game = GameplayTests.game

    def test_system_prompt_has_adult_tone_and_no_drawback_or_self_judging(self):
        import prompts
        state = world_state.WorldState()
        state.apply_template({"world": {"background": "修仙"},
                              "player": {"talents": [{"name": "混沌主宰", "tier": "炫彩", "drawback": "天劫如影随形"}]}})
        text = prompts.build_system_prompt(state, [])
        self.assertIn("【内容尺度——成人向】", text)
        self.assertIn("涉及性的角色一律是成年人", text)
        self.assertIn("【叙事质量】", text)
        self.assertNotIn("天劫如影随形", text)
        self.assertNotIn("由你或系统决定", text)

    def test_pool_has_no_drawbacks(self):
        import character_builder as cb
        pool = cb.prepare_pool({"talents": [{"name": "逆天", "tier": "炫彩", "drawback": "必有代价"}]})
        self.assertTrue(all(not item.get("drawback") for key in cb.POOL_TARGET_COUNTS for item in pool[key]))
        self.assertEqual(pool["_meta"]["version"], cb.POOL_VERSION)

    def test_talent_bonus_has_soft_cap(self):
        stacked = [("天命之子", "炫彩"), ("混沌主宰", "炫彩"), ("天命气运", "红")]
        self.assertEqual(judgment.talent_bonus("action", stacked), judgment.TALENT_BONUS_CAP)
        p = judgment.compute_judgment_probability("action", {}, stacked, 0, 0)
        self.assertTrue(0.95 < p < 0.99)
        self.assertGreater(judgment.talent_bonus("action", [("天命之子", "金")]), 0)

    def test_npc_memory_is_recorded_and_shown(self):
        llm = Replies(
            ("老掌柜收下灵石。", {"characters": {"老掌柜": {"description": "杂货铺掌柜", "attitude_toward_player": "友善",
                                                      "location": "山崖", "memory": "林舟预付了五块灵石订一把剑"}}}),
            ("三日后。", {"characters": {"老掌柜": {"memory": "林舟预付了五块灵石订一把剑"}}}),
            ("你又来了。", {"characters": {"老掌柜": {"memory": "林舟嫌剑太钝，说要退货"}}}),
        )
        game = self.game(llm)
        for action in ("订剑", "等三天", "退货"):
            game.run_turn(action)
        char = game.state.data["characters"]["老掌柜"]
        self.assertEqual(char["memories"], ["林舟预付了五块灵石订一把剑", "林舟嫌剑太钝，说要退货"])
        summary = game.state.to_summary()
        self.assertIn("往事：林舟预付了五块灵石订一把剑；林舟嫌剑太钝，说要退货", summary)

    def test_new_characters_start_affinity_from_attitude(self):
        state = world_state.WorldState()
        state.apply_template({"world": {"background": "修仙"},
                              "characters": {"师父": {"attitude_toward_player": "慈爱"}, "仇家": {"attitude_toward_player": "敌视"}}})
        self.assertEqual(state.data["characters"]["师父"]["affinity"], 55)
        self.assertEqual(state.data["characters"]["仇家"]["affinity"], 10)
        world_state.apply_update(state, {"characters": {"路人": {"description": "挑夫"}},
                                         "relationship_events": [{"subject": "新邻居", "event": "赠礼"}]})
        self.assertEqual(state.data["characters"]["路人"]["affinity"], 40)
        self.assertEqual(state.data["characters"]["新邻居"]["affinity"], 46)

    def test_summary_keeps_recent_and_present_characters_beyond_twelve(self):
        state = world_state.WorldState()
        state.data["location"]["name"] = "青石镇"
        for i in range(15):
            world_state.apply_update(state, {"characters": {f"旧人{i}": {"description": "x", "location": "远方"}}})
        world_state.apply_update(state, {"characters": {"新识": {"description": "刚认识", "location": "青石镇"}}})
        world_state.apply_update(state, {"characters": {"旧人0": {"memory": "又见面了"}}})
        summary = state.to_summary()
        self.assertIn("新识", summary)
        self.assertIn("旧人0", summary)


class SeasonsTests(GameFixture):
    """岁月模式：时间、打断、回血、幕后台词只由代码结算。"""

    def test_uninterrupted_month_advances_calendar_and_heals(self):
        llm = Replies(("这段日子过去了。", {"player_attributes": {"hp": 1}}))
        game = self.game(llm, hp=50)
        before = game.state.data["meta"]["turn"]
        with patch.object(random, "random", return_value=0.99):
            text = game.run_block("在洞府修炼", 30)
        self.assertEqual(text, "这段日子过去了。")
        self.assertEqual(game.state.data["calendar"]["days"], 30)
        self.assertIsNone(game.pending_block)
        self.assertEqual(game.state.data["meta"]["turn"], before + 1)
        # 回血由代码结算，且不超上限；模型返回的 hp=1 必须被忽略
        hp = game.state.data["player"]["attributes"]["hp"]
        self.assertEqual(hp, 50 + seasons.heal_amount(100, 30))
        self.assertLessEqual(hp, 100)
        self.assertNotEqual(hp, 1)
        self.assertEqual(game.state.data["last_block"]["interrupted"], False)

    def test_interrupt_in_first_slice_leaves_pending_block(self):
        llm = Replies(("修炼到第十日，有人来访。", {}))
        game = self.game(llm)
        with patch.object(random, "random", return_value=0.0):
            game.run_block("闭关一年", 360, seclusion=True)
        self.assertEqual(game.state.data["calendar"]["days"], 10)
        pending = game.pending_block
        self.assertEqual(pending["plan"], "闭关一年")
        self.assertEqual(pending["remaining"], 350)
        self.assertIs(pending["seclusion"], True)
        self.assertEqual(game.state.data["last_block"]["interrupted"], True)
        # 界面发起的安排只调用一次模型；结算简报就在这次调用里
        self.assertEqual(len(llm.messages), 1)
        brief = llm.messages[0][-1]["content"]
        self.assertIn("打断事件：", brief)

    def test_resume_block_completes_remaining_time(self):
        llm = Replies(("被打断了。", {}), ("剩余的日子过完了。", {}))
        game = self.game(llm)
        with patch.object(random, "random", return_value=0.0):
            game.run_block("闭关一年", 360, seclusion=True)
        self.assertEqual(game.state.data["calendar"]["days"], 10)
        with patch.object(random, "random", return_value=0.99):
            text = game.resume_block()
        self.assertEqual(text, "剩余的日子过完了。")
        self.assertEqual(game.state.data["calendar"]["days"], 360)
        self.assertIsNone(game.pending_block)
        self.assertEqual(game.state.data["last_block"]["interrupted"], False)

    def test_normal_turn_can_declare_time_block(self):
        llm = Replies(("（草稿）我这就去修炼。", {"time_block": {"plan": "在洞府修炼", "days": 30}}),
                      ("一个月过去了。", {}))
        game = self.game(llm)
        with patch.object(random, "random", return_value=0.99):
            text = game.run_turn("我闭关修炼一个月")
        # 第一次回复只是草稿，不发布；玩家看到的是结算后的叙事
        self.assertEqual(text, "一个月过去了。")
        self.assertEqual(game.state.data["calendar"]["days"], 30)

    def test_settlement_cannot_start_another_roll(self):
        llm = Replies(("这段时间过去了。", {"combat": {"enemy_name": "刺客", "enemy_power": 30, "action": "fight"}}))
        game = self.game(llm)
        with patch.object(random, "random", return_value=0.99):
            game.run_block("在洞府修炼", 30)
        self.assertIsNone(game.state.data["last_combat"])
        self.assertEqual(game.state.data["calendar"]["days"], 30)

    def test_offscreen_is_model_only_and_capped(self):
        llm = Replies(("日子过去了。", {"offscreen": ["天弃教在打探消息", "青阳宗换了执事", "  "] * 5}))
        game = self.game(llm)
        with patch.object(random, "random", return_value=0.99):
            game.run_block("在洞府修炼", 30)
        off = game.state.data["offscreen"]
        self.assertLessEqual(len(off), world_state.MAX_OFFSCREEN)
        self.assertEqual(len(off), 2)          # 去重 + 丢弃空白
        summary = game.state.to_summary()
        player = game.state.to_player_summary()
        self.assertIn("幕后进展", summary)
        self.assertIn("天弃教在打探消息", summary)
        self.assertNotIn("幕后进展", player)
        self.assertNotIn("天弃教在打探消息", player)

    def test_age_advances_across_a_year(self):
        llm = Replies(("一年过去了。", {}))
        game = self.game(llm)
        game.state.data["player"]["attributes"]["年龄"] = 16
        with patch.object(random, "random", return_value=0.99):
            game.run_block("闭关一年", 360, seclusion=True)
        self.assertEqual(game.state.data["calendar"]["days"], 360)
        self.assertEqual(game.state.data["player"]["attributes"]["年龄"], 17)

    def test_block_does_not_run_after_death(self):
        game = self.game(Replies())
        game.state.mark_dead("测试结局")
        text = game.run_block("在洞府修炼", 30)
        self.assertIn("已经结束", text)
        self.assertEqual(game.state.data["meta"]["turn"], 0)
        self.assertEqual(game.state.data["calendar"]["days"], 0)

    def test_seasons_prompt_section_present(self):
        self.assertIn("【岁月模式——一段时间的安排】", prompts.SYSTEM_PROMPT)
        self.assertIn("time_block", prompts.SYSTEM_PROMPT)
        self.assertIn("底线", prompts.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
