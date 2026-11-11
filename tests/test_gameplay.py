"""游戏结果与状态必须一致；这些回归检查不调用API、不使用玩家存档。"""
import copy
import json
import math
import os
import random
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import combat
import config
import dynamics
import judgment
import memory
import prompts
import seasons
import storage
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

    def test_fatal_risk_has_no_pity_bonus_and_ordinary_pity_is_gradual(self):
        # P1-7：保底从「到阈值直接 1.0」改为渐进加成；致命风险的判定仍然不吃保底
        base = judgment.compute_judgment_probability("action", {}, [], 0, 0)
        ordinary = judgment.compute_judgment_probability("action", {}, [], 0, 99)
        fatal = judgment.compute_judgment_probability("action", {}, [], 0, 99, allow_pity=False)
        self.assertAlmostEqual(fatal, base)
        self.assertGreater(ordinary, fatal)
        self.assertLess(ordinary, 1.0)  # 不再是必成功
        with patch.object(random, "random", return_value=0.999999):
            fatal_success, _ = judgment.resolve_judgment("action", {}, [], 0, {"fails": 99}, allow_pity=False)
        self.assertFalse(fatal_success)

    def test_affinity_factor_is_set_by_code_not_model(self):
        """P1-6：模型抢先填的「当前好感度」被代码算出的真实好感覆盖。"""
        game = self.game(Replies(), hp=100)
        game.state.data["characters"] = {"甲": {"description": "路人", "attitude_toward_player": "友善",
                                               "affinity": 10, "met_turn": 0}}
        game.llm = Replies(
            ("你试图靠近。", {"judgment": {"type": "relationship", "subject": "甲",
                                          "factors": {"当前好感度": 3}}}),
            ("她退了半步。", {}))
        captured = {}
        real = judgment.resolve_judgment

        def spy(jtype, factors, talents, item_bonus, pity, allow_pity=True, certain_failure=False):
            captured[jtype] = dict(factors)
            return real(jtype, factors, talents, item_bonus, pity,
                        allow_pity=allow_pity, certain_failure=certain_failure)

        with patch.object(judgment, "resolve_judgment", side_effect=spy), \
                patch.object(random, "random", return_value=0.999999):
            game.run_turn("向她表白")
        self.assertLess(captured["relationship"]["当前好感度"], 0)

    def test_persuasion_uses_the_right_affinity_factor_key(self):
        """P1-6：说服判定注入的是「对方好感度」，不是「当前好感度」。"""
        game = self.game(Replies(), hp=100)
        game.state.data["characters"] = {"甲": {"description": "路人", "attitude_toward_player": "友善",
                                               "affinity": 10, "met_turn": 0}}
        game.llm = Replies(
            ("你开口劝说。", {"judgment": {"type": "persuasion", "subject": "甲",
                                          "factors": {"对方好感度": 3}}}),
            ("她摇了摇头。", {}))
        captured = {}
        real = judgment.resolve_judgment

        def spy(jtype, factors, talents, item_bonus, pity, allow_pity=True, certain_failure=False):
            captured[jtype] = dict(factors)
            return real(jtype, factors, talents, item_bonus, pity,
                        allow_pity=allow_pity, certain_failure=certain_failure)

        with patch.object(judgment, "resolve_judgment", side_effect=spy), \
                patch.object(random, "random", return_value=0.999999):
            game.run_turn("劝她留下")
        self.assertLess(captured["persuasion"]["对方好感度"], 0)

    def test_injury_can_exhaust_life(self):
        llm = Replies(
            ("危险尝试", {"judgment": {"type": "action", "failure_consequence": "injury", "failure_damage": 40}}),
            ("伤势导致死亡。", {"options": []}),
        )
        game = self.game(llm, hp=10)
        with patch.object(random, "random", return_value=0.999999):
            game.run_turn("冒险尝试")
        self.assertTrue(game.is_over)

    def test_injury_severity_controls_damage(self):
        """P1-8：掉多少血由档位决定，模型填的数字不再直接生效。"""
        cases = (("light", 10), ("medium", 25), ("heavy", 45))
        for severity, expected_damage in cases:
            with self.subTest(severity=severity):
                llm = Replies(
                    ("危险尝试", {"judgment": {"type": "action", "failure_consequence": "injury",
                                              "failure_severity": severity}}),
                    ("你受了伤。", {}),
                )
                game = self.game(llm, hp=100)
                with patch.object(random, "random", return_value=0.999999), \
                        patch.object(random, "uniform", return_value=1.0):
                    game.run_turn("硬闯")
                hp_after = game.state.data["player"]["attributes"]["hp"]
                self.assertEqual(100 - hp_after, expected_damage)

    def test_huge_legacy_failure_damage_is_mapped_to_severity(self):
        """兼容旧字段：failure_damage=99999 按比例映射成重伤，扣血仍 ≤ 生命上限 54%。"""
        llm = Replies(
            ("危险尝试", {"judgment": {"type": "action", "failure_consequence": "injury",
                                      "failure_damage": 99999}}),
            ("你受了重伤。", {}),
        )
        game = self.game(llm, hp=100)
        with patch.object(random, "random", return_value=0.999999):
            game.run_turn("硬闯")
        hp_after = game.state.data["player"]["attributes"]["hp"]
        self.assertGreaterEqual(hp_after, 46)

    def test_injury_damage_stays_within_severity_range(self):
        """审查 L3：不 patch uniform，锁住 0.8~1.2 的实际浮动区间。"""
        for severity, low, high in (("light", 8, 12), ("medium", 20, 30), ("heavy", 36, 54)):
            with self.subTest(severity=severity):
                llm = Replies(("危险", {"judgment": {"type": "action", "failure_consequence": "injury",
                                                    "failure_severity": severity}}),
                              ("你受了伤。", {}))
                game = self.game(llm, hp=100)
                with patch.object(random, "random", return_value=0.999999):
                    game.run_turn("冒险")
                damage = 100 - game.state.data["player"]["attributes"]["hp"]
                self.assertTrue(low <= damage <= high, (severity, damage))

    def test_days_passed_survives_a_judgment_turn(self):
        """审查 M1：判定回合第一段声明的 days_passed 不能被 _prepare_roll 丢掉。"""
        llm = Replies(("赶路", {"days_passed": 2, "judgment": {"type": "action"}}), ("到了。", {}))
        game = self.game(llm)
        with patch.object(random, "random", return_value=0.0):
            game.run_turn("赶两天路")
        self.assertEqual(game.state.data["calendar"]["days"], 2)

    def test_days_passed_survives_a_combat_turn(self):
        llm = Replies(("遇袭", {"days_passed": 3, "combat": {"enemy_name": "狼", "enemy_power": 5,
                                                             "action": "fight"}}),
                      ("你赢了。", {}))
        game = self.game(llm)
        with patch.object(random, "random", return_value=0.0):
            game.run_turn("赶三天路后被狼袭击")
        self.assertEqual(game.state.data["calendar"]["days"], 3)

    def test_affinity_override_covers_legacy_characters(self):
        """审查 L1：旧存档角色没有 affinity 字段时，模型填的好感因素也要被代码覆盖。"""
        game = self.game(Replies(), hp=100)
        game.state.data["characters"] = {"甲": {"description": "旧存档角色"}}
        game.llm = Replies(("靠近", {"judgment": {"type": "relationship", "subject": "甲",
                                                 "factors": {"当前好感度": 3}}}),
                           ("她没理你。", {}))
        captured = {}
        real = judgment.resolve_judgment

        def spy(jtype, factors, talents, item_bonus, pity, allow_pity=True, certain_failure=False):
            captured[jtype] = dict(factors)
            return real(jtype, factors, talents, item_bonus, pity,
                        allow_pity=allow_pity, certain_failure=certain_failure)

        with patch.object(judgment, "resolve_judgment", side_effect=spy), \
                patch.object(random, "random", return_value=0.999999):
            game.run_turn("接近她")
        self.assertLess(captured["relationship"]["当前好感度"], 0)

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

    def test_failed_second_call_rolls_back_days_passed(self):
        """P1-11 的日历推进发生在提交阶段，第二次调用失败时不能被留下。"""
        llm = Replies(("迎战", {"combat": {"enemy_name": "强敌", "enemy_power": 20, "action": "fight"},
                                "days_passed": 5}), LLMError("模拟网络失败"))
        game = self.game(llm)
        with patch.object(random, "random", return_value=0.999999):
            with self.assertRaises(LLMError):
                game.run_turn("追了两天路再迎战")
        self.assertEqual(game.state.data["calendar"]["days"], 0)

    # ---- P2-13：非消耗道具主动使用，加成只算一次 ----
    def test_non_consumable_item_bonus_counted_once(self):
        game = self.game(Replies(), hp=100)
        game.state.data["inventory"] = [{"name": "寻宝罗盘", "quantity": 1, "description": "寻宝"}]
        game.llm = Replies(("仔细搜寻。", {"judgment": {"type": "treasure", "subject": "山洞",
                                                       "use_item": "寻宝罗盘"}}),
                           ("你找到了一些线索。", {}))
        captured = {}
        real = judgment.resolve_judgment

        def spy(jtype, factors, talents, item_bonus, pity, allow_pity=True, certain_failure=False):
            captured["item_bonus"] = item_bonus
            return real(jtype, factors, talents, item_bonus, pity,
                        allow_pity=allow_pity, certain_failure=certain_failure)

        with patch.object(judgment, "resolve_judgment", side_effect=spy):
            game.run_turn("用寻宝罗盘找宝")
        expected = judgment.ITEM_MODIFIERS["寻宝罗盘"]["treasure"]
        self.assertAlmostEqual(captured["item_bonus"], expected)

    # ---- P2-15：敌方战力的合法性检查 ----
    def test_invalid_enemy_power_is_sanitized(self):
        bad_values = (float("nan"), float("inf"), True, -50, 10 ** 9)
        for bad in bad_values:
            with self.subTest(enemy_power=bad):
                llm = Replies(("迎战", {"combat": {"enemy_name": "怪", "enemy_power": bad,
                                                   "action": "fight"}}),
                              ("打完了。", {}))
                game = self.game(llm)
                with patch.object(random, "random", return_value=0.5):
                    game.run_turn("迎战怪物")
                recorded = game.state.data["last_combat"]["enemy_power"]
                self.assertIsInstance(recorded, (int, float))
                self.assertTrue(math.isfinite(recorded))
                self.assertGreaterEqual(recorded, 0)
                self.assertLessEqual(recorded, (game.state.data["combat"]["player_power"]
                                                + 6 * game.state.data["combat"]["realm_gap"]))

    def test_missing_enemy_power_falls_back_to_player_power(self):
        llm = Replies(("迎战", {"combat": {"enemy_name": "怪", "action": "fight"}}), ("打完了。", {}))
        game = self.game(llm)
        with patch.object(random, "random", return_value=0.5):
            game.run_turn("迎战")
        self.assertEqual(game.state.data["last_combat"]["enemy_power"],
                         game.state.data["combat"]["player_power"])

    def test_dirty_combat_anchor_is_sanitized(self):
        """P2-15 延伸：旧存档/手改数据里的脏战力锚点不能把战斗流程弄崩。"""
        llm = Replies(("迎战", {"combat": {"enemy_name": "怪", "enemy_power": 10, "action": "fight"}}),
                      ("打完了。", {}))
        game = self.game(llm)
        game.state.data["combat"] = {"player_power": "10", "realm_gap": float("nan")}
        with patch.object(random, "random", return_value=0.5):
            game.run_turn("迎战")
        self.assertFalse(game.is_over)
        self.assertIn("last_combat", game.state.data)


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

    def test_death_is_reported_on_failed_escape_but_never_on_victory(self):
        # P0-1：胜负与生死解耦——胜利永不致死，但逃跑失败/落败仍会致死
        with patch.object(random, "random", return_value=0):
            victory = combat.resolve(10, 10, 5, "fight", combat.curve_params("realm_gap"), 1)
        self.assertFalse(victory["player_down"])
        self.assertNotIn("已死亡", combat.gm_brief(victory, 1))
        with patch.object(random, "random", return_value=0.999999):
            escape = combat.resolve(10, 10, 5, "flee", combat.curve_params("realm_gap"), 1)
        self.assertTrue(escape["player_down"])
        self.assertIn("已死亡", combat.gm_brief(escape, 1))

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

    def test_lethal_judgment_spends_jade_protection(self):
        """P0-3：非战斗判定的致死也走保命能力；用完之后再致命就真的死。"""
        request = {"judgment": {"type": "action", "subject": "坠崖", "reason": "从千丈崖坠落",
                                "factors": {f"不利{i}": -3 for i in range(6)},
                                "failure_consequence": "death"}}
        llm = Replies(("跳", request), ("你坠了下去。", {}),
                      ("再跳", request), ("你再次坠了下去。", {}))
        game = self.game(llm, hp=10)
        game.state.data["player"]["talents"] = [{"name": "古玉护佑", "description": "抵挡致命一击"}]
        with patch.object(random, "random", return_value=0.999999):
            game.run_turn("跳下悬崖")
            self.assertFalse(game.is_over)
            self.assertEqual(game.state.data["player"]["attributes"]["hp"], 1)
            self.assertEqual(game.state.data["player"]["death_protections_used"], {"古玉护佑": 1})
            game.run_turn("再次跳下悬崖")
        self.assertTrue(game.is_over)

    def test_certain_failure_ignores_jade_protection(self):
        """certain_failure 的必死行为没有生还条件，保命能力不生效。"""
        request = {"judgment": {"type": "action", "subject": "火海", "reason": "跳进致命火海",
                                "certain_failure": True, "failure_consequence": "death"}}
        llm = Replies(("跳", request), ("你没能出来。", {}))
        game = self.game(llm, hp=50)
        game.state.data["player"]["talents"] = [{"name": "古玉护佑", "description": "抵挡致命一击"}]
        game.run_turn("跳进火海")
        self.assertTrue(game.is_over)
        self.assertEqual(game.state.data["player"].get("death_protections_used") or {}, {})

    def test_injury_judgment_that_empties_hp_spends_jade_protection(self):
        """P0-3：受伤判定把血扣到 0 时，也算致命一击，先走保命能力。"""
        request = {"judgment": {"type": "action", "subject": "巨石", "reason": "被巨石砸中",
                                "factors": {f"不利{i}": -3 for i in range(6)},
                                "failure_consequence": "injury", "failure_damage": 99999}}
        llm = Replies(("躲", request), ("巨石砸了下来。", {}))
        game = self.game(llm, hp=10)  # 重伤档最多扣 54%，10 点血必定被打到 0
        game.state.data["player"]["talents"] = [{"name": "古玉护佑", "description": "抵挡致命一击"}]
        with patch.object(random, "random", return_value=0.999999):
            game.run_turn("硬接巨石")
        self.assertFalse(game.is_over)
        self.assertEqual(game.state.data["player"]["attributes"]["hp"], 1)
        self.assertEqual(game.state.data["player"]["death_protections_used"], {"古玉护佑": 1})


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

    def test_safe_int_handles_overflow_strings(self):
        """审查 L4：'1e999' / 'inf' 这类字符串不能抛 OverflowError 打断整轮。"""
        self.assertIsNone(world_state.safe_int("1e999"))
        self.assertIsNone(world_state.safe_int("inf"))
        self.assertIsNone(world_state.safe_int("-inf"))
        self.assertIsNone(world_state.safe_int("nan"))
        self.assertEqual(world_state.safe_int("12"), 12)

    def test_overflow_days_passed_does_not_break_turn(self):
        llm = Replies(("走了很久。", {"days_passed": "1e999"}))
        game = self.game(llm)
        game.run_turn("走了很久")
        self.assertEqual(game.state.data["calendar"]["days"], 0)

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


class JudgmentIntegrityTests(unittest.TestCase):
    """P1-5 / P1-7：因素堆叠有上限；保底是渐进加成而不是必成。"""

    def test_unknown_factors_cannot_stack_to_guarantee(self):
        factors = {f"模型自造因素{i}": 3 for i in range(10)}
        p = judgment.compute_judgment_probability("relationship", factors, [], 0, 0)
        self.assertLess(p, 0.5)

    def test_known_factors_total_is_capped(self):
        factors = {name: 3 for name in judgment.JUDGMENT_TYPES["action"]["factors"]}
        p = judgment.compute_judgment_probability("action", factors, [], 0, 0)
        self.assertLess(p, 0.999)
        self.assertGreater(p, 0.95)  # 仍然很有利，但不会变成必成

    def test_pity_is_gradual_not_guaranteed(self):
        factors = {name: -3 for name in judgment.JUDGMENT_TYPES["relationship"]["factors"]}
        p = judgment.compute_judgment_probability("relationship", factors, [], 0, 12)
        self.assertLess(p, 0.5)

    def test_known_and_unknown_factors_share_the_cap(self):
        known = {name: 3 for name in judgment.JUDGMENT_TYPES["action"]["factors"]}
        unknown = {f"自造{i}": 3 for i in range(10)}
        p = judgment.compute_judgment_probability("action", {**known, **unknown}, [], 0, 0)
        self.assertLessEqual(p, judgment._sigmoid(judgment.FACTOR_TOTAL_CAP) + 1e-9)


class AttributeFactorTests(GameFixture):
    """P2-19：有对应玩家属性的判定因素由代码换算并覆盖模型填写。"""

    def _capture_judgment(self, game, request, draft, outcome):
        captured = {}
        real = judgment.resolve_judgment

        def spy(jtype, factors, talents, item_bonus, pity, allow_pity=True, certain_failure=False):
            captured[jtype] = dict(factors)
            return real(jtype, factors, talents, item_bonus, pity,
                        allow_pity=allow_pity, certain_failure=certain_failure)

        game.llm = Replies((draft, request), (outcome, {}))
        with patch.object(judgment, "resolve_judgment", side_effect=spy), \
                patch.object(random, "random", return_value=0.999999):
            game.run_turn("尝试")
        return captured

    def test_attribute_factor_conversion(self):
        cases = [(None, 0), ("文字", 0), (0, 0), (8, 1), (-8, -1), (30, 3), (100, 3),
                 (float("inf"), 0), (float("nan"), 0)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(judgment.attribute_factor(value), expected)

    def test_treasure_factor_comes_from_fuyuan(self):
        game = self.game(Replies(), hp=100)
        game.state.data["player"]["attributes"]["福缘"] = 30
        captured = self._capture_judgment(
            game,
            {"judgment": {"type": "treasure", "subject": "山洞", "factors": {"福缘/运气": -3}}},
            "寻宝。", "没找到。")
        self.assertEqual(captured["treasure"]["福缘/运气"], 3)

    def test_alias_factor_key_is_replaced(self):
        game = self.game(Replies(), hp=100)
        game.state.data["player"]["attributes"]["福缘"] = 10
        captured = self._capture_judgment(
            game,
            {"judgment": {"type": "treasure", "subject": "山洞", "factors": {"福缘运气": -3}}},
            "寻宝。", "没找到。")
        factors = captured["treasure"]
        self.assertEqual(factors["福缘/运气"], 1)
        self.assertNotIn("福缘运气", factors)

    def test_persuasion_factor_comes_from_charm(self):
        game = self.game(Replies(), hp=100)
        game.state.data["player"]["attributes"]["魅力"] = -20
        captured = self._capture_judgment(
            game,
            {"judgment": {"type": "persuasion", "subject": "路人", "factors": {"口才/魅力": 3}}},
            "劝说。", "对方摇头。")
        self.assertEqual(captured["persuasion"]["口才/魅力"], -2)

    def test_missing_attribute_is_neutral(self):
        game = self.game(Replies(), hp=100)
        captured = self._capture_judgment(
            game,
            {"judgment": {"type": "treasure", "subject": "山洞", "factors": {"福缘/运气": 3}}},
            "寻宝。", "没找到。")
        self.assertEqual(captured["treasure"]["福缘/运气"], 0)

    def test_other_types_are_not_touched(self):
        game = self.game(Replies(), hp=100)
        game.state.data["player"]["attributes"]["福缘"] = 30
        captured = self._capture_judgment(
            game,
            {"judgment": {"type": "insight", "subject": "谜题", "factors": {"智力/洞察": -3}}},
            "思考。", "没想通。")
        self.assertEqual(captured["insight"]["智力/洞察"], -3)


class SummaryTruncationTests(unittest.TestCase):
    """P1-9：摘要截断时保留最新内容，特殊道具永远可见。"""

    def test_newest_plot_flag_is_in_summary(self):
        state = world_state.WorldState()
        for i in range(25):
            world_state.apply_update(state, {"plot_flags": {f"标记{i}": True}})
        text = state.to_summary()
        self.assertIn("标记24", text)
        self.assertNotIn("标记0=", text)
        self.assertIn("未列出", text)

    def test_updated_old_flag_moves_to_end(self):
        state = world_state.WorldState()
        for i in range(25):
            world_state.apply_update(state, {"plot_flags": {f"标记{i}": True}})
        world_state.apply_update(state, {"plot_flags": {"标记0": "完成"}})
        self.assertIn("标记0=完成", state.to_summary())

    def test_special_item_survives_truncation(self):
        state = world_state.WorldState()
        world_state.apply_update(state, {"inventory_add": [{"name": "寻宝罗盘", "quantity": 1}]})
        for i in range(20):
            world_state.apply_update(state, {"inventory_add": [{"name": f"杂物{i}", "quantity": 1}]})
        text = state.to_summary()
        self.assertIn("寻宝罗盘", text)
        self.assertIn("杂物19", text)
        self.assertIn("另有", text)


class GameplayAuthorityTests(GameFixture):
    """P0-2：战力 / 好感 / 相识回合 / 生命上限是系统独占字段，模型一句话改不动。"""

    def _fresh(self):
        state = world_state.WorldState()
        state.apply_template({
            "world": {"background": "修仙"},
            "player": {"name": "林舟", "attributes": {"hp": 100, "max_hp": 100}},
            "characters": {"甲": {"description": "路人", "attitude_toward_player": "友善"}},
        })
        return state

    def test_model_cannot_write_affinity_or_met_turn(self):
        state = self._fresh()
        before = state.data["characters"]["甲"]["affinity"]
        world_state.apply_update(state, {"characters": {"甲": {
            "affinity": 9999, "met_turn": -1000, "description": "换了描述"}}})
        self.assertEqual(state.data["characters"]["甲"]["affinity"], before)
        self.assertEqual(state.data["characters"]["甲"]["met_turn"], 0)
        self.assertEqual(state.data["characters"]["甲"]["description"], "换了描述")  # 合法字段照常更新

    def test_new_character_met_turn_is_current_turn(self):
        state = self._fresh()
        world_state.apply_update(state, {"characters": {"新人": {
            "description": "刚认识", "affinity": 9999, "met_turn": -1000}}})
        char = state.data["characters"]["新人"]
        self.assertEqual(char["met_turn"], 0)
        self.assertLessEqual(char["affinity"], 100)

    def test_relationship_events_still_move_affinity(self):
        state = self._fresh()
        before = state.data["characters"]["甲"]["affinity"]
        world_state.apply_update(state, {"relationship_events": [{"subject": "甲", "event": "赠礼"}]})
        self.assertGreater(state.data["characters"]["甲"]["affinity"], before)

    def test_relationship_events_are_capped_per_turn(self):
        """P1-10：同一人单轮写 4 次「救命之恩」只结算最强的一条，且单轮变化 ≤ 30。"""
        state = self._fresh()
        before = state.data["characters"]["甲"]["affinity"]
        world_state.apply_update(state, {"relationship_events": [
            {"subject": "甲", "event": "救命之恩"}] * 4})
        char = state.data["characters"]["甲"]
        self.assertLessEqual(char["affinity"], before + 30)
        self.assertEqual(char["interactions"], 1)

    def test_relationship_event_keeps_the_strongest(self):
        state = self._fresh()
        before = state.data["characters"]["甲"]["affinity"]
        world_state.apply_update(state, {"relationship_events": [
            {"subject": "甲", "event": "冒犯"}, {"subject": "甲", "event": "救命之恩"}]})
        self.assertEqual(state.data["characters"]["甲"]["affinity"], min(before + 30, 100))

    def test_model_cannot_set_power_directly(self):
        state = self._fresh()
        world_state.apply_update(state, {"player_power": 1e9})
        power = state.data["combat"]["player_power"]
        self.assertGreater(power, 10)          # 合法的小幅成长仍然生效
        self.assertLessEqual(power, 10 + 5)    # 但只允许 +1 个 realm_gap

    def test_invalid_power_is_ignored(self):
        for bad in (float("inf"), float("nan"), -5, 0, True):
            with self.subTest(power=bad):
                state = self._fresh()
                world_state.apply_update(state, {"player_power": bad})
                self.assertEqual(state.data["combat"]["player_power"], 10)

    def test_block_settlement_relaxes_power_cap_by_days(self):
        llm = Replies(("你闭关修炼。", {"player_power": 1000}))
        game = self.game(llm)
        fixed = {"planned": 360, "elapsed": 360, "remaining": 0, "seclusion": True,
                 "flavors": [], "interrupt": None}
        with patch.object(seasons, "roll_block", return_value=fixed):
            game.run_block("闭关", 360)  # 360 天 → realm_gap × max(1, 360/180) = 10
        power = game.state.data["combat"]["player_power"]
        self.assertGreater(power, 15)   # 比场景回合的 +5 更宽
        self.assertLessEqual(power, 20)

    def test_model_cannot_set_max_hp_directly(self):
        state = self._fresh()
        world_state.apply_update(state, {"player_attributes": {"max_hp": 10 ** 9}})
        self.assertLessEqual(state.data["player"]["attributes"]["max_hp"], 150)

    def test_max_hp_cannot_collapse_in_one_turn(self):
        state = self._fresh()
        world_state.apply_update(state, {"player_attributes": {"max_hp": 1}})
        self.assertGreaterEqual(state.data["player"]["attributes"]["max_hp"], 50)

    def test_hp_is_capped_by_lowered_max_hp(self):
        state = self._fresh()
        state.data["player"]["attributes"]["hp"] = 100
        world_state.apply_update(state, {"player_attributes": {"max_hp": 50}})
        self.assertLessEqual(state.data["player"]["attributes"]["hp"],
                             state.data["player"]["attributes"]["max_hp"])

    def test_combat_damage_scales_with_max_hp(self):
        params = combat.curve_params("realm_gap")
        with patch.object(random, "random", return_value=0.999999):
            small = combat.resolve(10, 10, 5, "fight", params, 100, max_hp=100)
            big = combat.resolve(10, 10, 5, "fight", params, 500, max_hp=200)
        self.assertEqual(small["player_damage"], 20)          # 20% × 100
        self.assertEqual(big["player_damage"], 40)            # 20% × 200

    def test_junk_hp_values_are_rejected(self):
        """验证补丁：hp 是系统独占字段，字符串/布尔/NaN 不能写进状态。"""
        state = self._fresh()
        for junk in ("hp", "", True, None, float("nan"), float("inf"), [1], {"x": 1}):
            with self.subTest(junk=junk):
                world_state.apply_update(state, {"player_attributes": {"hp": junk}})
                hp = state.data["player"]["attributes"]["hp"]
                self.assertIsInstance(hp, (int, float))
                self.assertGreaterEqual(hp, 0)
        world_state.apply_update(state, {"player_attributes": {"hp": -50}})
        self.assertEqual(state.data["player"]["attributes"]["hp"], 0)

    def test_max_hp_has_absolute_ceiling(self):
        """验证补丁：每轮 ×1.5 的成长不能把生命上限滚成天文数字。"""
        state = self._fresh()
        for _ in range(100):
            world_state.apply_update(state, {"player_attributes": {"max_hp": 10 ** 9}})
        self.assertLessEqual(state.data["player"]["attributes"]["max_hp"], world_state.MAX_HP_CEILING)

    def test_inventory_ignores_non_string_names(self):
        state = self._fresh()
        world_state.apply_update(state, {"inventory_add": [{"name": 7, "quantity": 1},
                                                           {"name": "", "quantity": 1},
                                                           {"name": "灵石", "quantity": 2}]})
        names = [i.get("name") for i in state.data["inventory"]]
        self.assertEqual(names, ["灵石"])

    # ---- P2-14：钱不够也能买 ----
    def test_insufficient_currency_never_goes_negative(self):
        state = self._fresh()
        world_state.apply_update(state, {"inventory_add": [{"name": "灵石", "quantity": 5}]})
        world_state.apply_update(state, {"inventory_remove": [{"name": "灵石", "quantity": 50}]})
        self.assertFalse(any(i.get("name") == "灵石" for i in state.data["inventory"]))
        self.assertTrue(any("灵石不足" in e and "需 50" in e and "仅有 5" in e
                            for e in state.data["event_log"]), state.data["event_log"])

    def test_partial_currency_removal_keeps_remainder(self):
        state = self._fresh()
        world_state.apply_update(state, {"inventory_add": [{"name": "灵石", "quantity": 5}]})
        world_state.apply_update(state, {"inventory_remove": [{"name": "灵石", "quantity": 3}]})
        entry = next(i for i in state.data["inventory"] if i.get("name") == "灵石")
        self.assertEqual(entry["quantity"], 2)
        self.assertFalse(any("灵石不足" in e for e in state.data["event_log"]))

    def test_apply_update_junk_fuzz_keeps_invariants(self):
        """固定种子的轻量模糊测试：畸形增量不能破坏任何系统不变量。"""
        rnd = random.Random(20261110)
        junk = [None, True, False, 0, -1, 1e9, float("inf"), float("nan"), "999", "", "hp", [], {}]
        state = self._fresh()
        for _ in range(300):
            update = {
                "player_attributes": {rnd.choice(["hp", "max_hp", "运气"]): rnd.choice(junk)
                                      for _ in range(rnd.randint(1, 2))},
                "player_power": rnd.choice(junk),
                "characters": {"甲": {"affinity": rnd.choice(junk), "met_turn": rnd.choice(junk)}},
                "inventory_add": [{"name": rnd.choice(["灵石", 7]), "quantity": rnd.choice(junk)}],
                "relationship_events": [{"subject": "甲", "event": rnd.choice(list(dynamics.RELATION_EVENTS))}
                                        for _ in range(rnd.randint(0, 4))],
                "attribute_events": [{"attribute": rnd.choice(["hp", "max_hp", "声望", "运气"]),
                                      "delta": rnd.choice(junk)}],
            }
            world_state.apply_update(state, update)
            attrs = state.data["player"]["attributes"]
            hp, max_hp = attrs.get("hp"), attrs.get("max_hp")
            self.assertIsInstance(hp, (int, float))
            self.assertIsInstance(max_hp, (int, float))
            self.assertLessEqual(hp, max_hp)
            self.assertGreaterEqual(hp, 0)
            self.assertLessEqual(max_hp, world_state.MAX_HP_CEILING)
            self.assertTrue(0 <= state.data["characters"]["甲"]["affinity"] <= 100)
            self.assertIsInstance(state.data["characters"]["甲"]["met_turn"], int)
            self.assertGreater(state.data["combat"]["player_power"], 0)
            for item in state.data["inventory"]:
                self.assertIsInstance(item.get("name"), str)
                self.assertGreater(item.get("quantity"), 0)

    # ---- 审查 H1：attribute_events 是生命上限的另一条旁路 ----
    def test_attribute_events_cannot_touch_vitals(self):
        state = self._fresh()
        world_state.apply_update(state, {"attribute_events": [
            {"attribute": "max_hp", "delta": 10 ** 9},
            {"attribute": "hp", "delta": -500},
        ]})
        attrs = state.data["player"]["attributes"]
        self.assertEqual(attrs["max_hp"], 100)
        self.assertEqual(attrs["hp"], 100)
        self.assertTrue(1 <= attrs["max_hp"] <= world_state.MAX_HP_CEILING)

    def test_max_hp_is_normalized_even_if_state_is_dirty(self):
        state = self._fresh()
        state.data["player"]["attributes"]["max_hp"] = -900
        state.data["player"]["attributes"]["hp"] = 500
        world_state.apply_update(state, {"plot_flags": {"x": True}})
        attrs = state.data["player"]["attributes"]
        self.assertTrue(1 <= attrs["max_hp"] <= world_state.MAX_HP_CEILING)
        self.assertGreaterEqual(attrs["hp"], 0)
        self.assertLessEqual(attrs["hp"], attrs["max_hp"])

    def test_get_max_hp_rejects_invalid_values(self):
        game = self.game(Replies(), hp=100)
        for bad in (float("inf"), float("nan"), -900, 0, "100", True):
            with self.subTest(bad=bad):
                game.state.data["player"]["attributes"]["max_hp"] = bad
                self.assertEqual(game._get_max_hp(), 100)

    def test_attribute_events_cannot_inflate_combat_damage(self):
        llm = Replies(("你休息。", {"attribute_events": [{"attribute": "max_hp", "delta": 10 ** 9}]}),
                      ("迎战", {"combat": {"enemy_name": "狼", "enemy_power": 20, "action": "fight"}}),
                      ("你输了。", {}))
        game = self.game(llm, hp=100)
        game.run_turn("休息")
        with patch.object(random, "random", return_value=0.999999):
            game.run_turn("迎战")
        self.assertFalse(game.is_over)
        self.assertGreater(game.state.data["player"]["attributes"]["hp"], 0)


class CombatDamageTests(unittest.TestCase):
    """P0-1：胜利伤害随优势衰减；胜利永不致死。"""

    params = combat.curve_params("realm_gap")

    def _fight(self, player_power, enemy_power, player_hp, roll=0.0):
        with patch.object(random, "random", return_value=roll):
            return combat.resolve(player_power, enemy_power, 5, "fight", self.params, player_hp)

    def test_overwhelming_win_takes_no_damage(self):
        """碾压弱敌不掉血（原 bug：每赢一场固定掉 8 点）。"""
        result = self._fight(100, 1, 5)
        self.assertEqual(result["outcome"], "win")
        self.assertEqual(result["player_damage"], 0)
        self.assertEqual(result["player_hp_after"], 5)
        self.assertFalse(result["player_down"])

    def test_evenly_matched_win_leaves_one_hp(self):
        """势均力敌的惨胜最多打到 1 点生命，不能判死。"""
        result = self._fight(10, 10, 3)
        self.assertEqual(result["outcome"], "win")
        self.assertEqual(result["player_hp_after"], 1)
        self.assertFalse(result["player_down"])

    def test_win_damage_decays_with_advantage(self):
        """领先越少伤得越重：0.4 倍线性衰减，领先一个段位即为 0。"""
        ratio = combat.win_damage_multiplier
        self.assertEqual(ratio(10, 10, 5), 0.4)           # 势均力敌
        self.assertGreater(ratio(11, 10, 5), 0.0)         # 小优势仍有伤
        self.assertLess(ratio(13, 10, 5), ratio(11, 10, 5))
        self.assertEqual(ratio(15, 10, 5), 0.0)           # 领先一个段位：无伤
        self.assertEqual(ratio(16, 10, 5), 0.0)
        self.assertEqual(ratio(10, 20, 5), 0.4)           # 对手更强：负伤


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

    # ---- P1-11：场景模式的整天也要计入日历 ----
    def test_days_passed_advances_calendar(self):
        llm = Replies(("你睡了一夜。", {"days_passed": 2}))
        game = self.game(llm)
        game.run_turn("睡一觉")
        self.assertEqual(game.state.data["calendar"]["days"], 2)

    def test_days_passed_is_clamped_to_nine(self):
        llm = Replies(("你睡了好久。", {"days_passed": 500}))
        game = self.game(llm)
        game.run_turn("长睡")
        self.assertEqual(game.state.data["calendar"]["days"], 9)

    def test_days_passed_crosses_a_year_and_adds_age(self):
        llm = Replies(("等了几天。", {"days_passed": 9}))
        game = self.game(llm)
        game.state.data["player"]["attributes"]["年龄"] = 20
        game.state.data["calendar"] = {"days": 356}
        game.run_turn("等待")
        self.assertEqual(game.state.data["calendar"]["days"], 365)
        self.assertEqual(game.state.data["player"]["attributes"]["年龄"], 21)

    def test_failed_block_narrative_rolls_back_calendar(self):
        """岁月模式先推进日历再让模型叙述：叙述失败时整轮回滚，日历与回合数不变。"""
        fixed = {"planned": 30, "elapsed": 30, "remaining": 0, "seclusion": True,
                 "flavors": [], "interrupt": None}
        llm = Replies(LLMError("模拟网络失败"))
        game = self.game(llm)
        with patch.object(seasons, "roll_block", return_value=fixed):
            with self.assertRaises(LLMError):
                game.run_block("闭关一月", 30, seclusion=True)
        self.assertEqual(game.state.data["calendar"]["days"], 0)
        self.assertEqual(game.state.data["meta"]["turn"], 0)


class CompressionBackoffTests(GameFixture):
    """P2-16：压缩一直失败时，缓冲有上限且不会每轮都重试昂贵的压缩调用。"""

    class _NoSummaryLLM:
        def __init__(self):
            self.summary_calls = 0

        def generate(self, messages):
            return "叙事。", {"turn_event": "一件事"}

        def summarize(self, messages):
            self.summary_calls += 1
            raise LLMError("模拟压缩失败")

    def test_buffer_is_capped(self):
        mem = memory.Memory()
        for i in range(120):
            mem.add_user("玩家" + str(i))
            mem.add_assistant("主持" + str(i))
        self.assertLessEqual(len(mem.buffer), config.MAX_BUFFER_ENTRIES)
        self.assertEqual(mem.buffer[-1]["content"], "主持119")

    def test_compression_backoff_after_repeated_failures(self):
        llm = self._NoSummaryLLM()
        game = self.game(llm)
        attempts = []
        for i in range(30):
            before = llm.summary_calls
            game.run_turn("行动" + str(i))
            if llm.summary_calls > before:
                attempts.append(i + 1)
        # 第 20 轮首次触发；连续 3 次失败后退避 5 轮，第 27 轮再试。
        self.assertEqual(attempts, [20, 21, 22, 27, 28, 29], attempts)
        self.assertLessEqual(len(game.memory.buffer), config.MAX_BUFFER_ENTRIES)


class TempSlotPruneTests(unittest.TestCase):
    """P2-17：只清理 N 天前的临时槽，命名存档与当前会话槽不动。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wanjie-prune-")
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(config, "SAVE_DIR", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _touch(self, name, days_old):
        path = Path(self.tmp.name) / name
        path.write_text("{}", encoding="utf-8")
        stamp = time.time() - days_old * 86400
        os.utime(path, (stamp, stamp))
        return path

    def test_prune_only_removes_old_temp_slots(self):
        old_state = self._touch("state_临时-111111.json", 9)
        old_hist = self._touch("history_临时-111111.json", 9)
        fresh_state = self._touch("state_临时-222222.json", 1)
        named_old = self._touch("state_alice.json", 30)
        removed = config.prune_temp_slots(7)
        self.assertEqual(removed, ["临时-111111"])
        self.assertFalse(old_state.exists())
        self.assertFalse(old_hist.exists())
        self.assertTrue(fresh_state.exists())
        self.assertTrue(named_old.exists())  # 命名存档永不清理

    def test_keep_protects_current_session_slot(self):
        active = self._touch("state_临时-333333.json", 30)
        removed = config.prune_temp_slots(7, keep="临时-333333")
        self.assertEqual(removed, [])
        self.assertTrue(active.exists())


class StorageSQLiteTests(unittest.TestCase):
    """路线图阶段 1：SQLite 审计层（建库 / 快照回放 / 迁移 / 可选双写）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wanjie-db-")
        self.addCleanup(self.tmp.cleanup)
        for module in (memory, world_state, config):
            patcher = patch.object(module, "SAVE_DIR", self.tmp.name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_recall_finds_early_needles(self):
        import json, os, random, tempfile, storage
        from world_state import WorldState
        random = random.Random(3)
        names = ["陆青鸢", "赵执事", "阿蛮", "无名老叟", "周掌柜", "林舟"]
        places = ["丹堂", "后山", "坊市", "外门执事堂", "断崖石室"]
        needles = {
            5:   ("陆青鸢", "陆青鸢压低声音：进藏经阁的暗号是青灯照壁。", "藏经阁的暗号", "青灯照壁"),
            50:  ("周掌柜", "周掌柜立下字据：欠你三百二十七块灵石，秋后归还。", "周掌柜欠我多少灵石", "三百二十七"),
            150: ("林舟", "林舟答应你：三日内替你寻回那枚碎裂的玄龟玉佩。", "林舟答应帮我找什么", "玄龟玉佩"),
        }
        db = storage.SaveDB(os.path.join(self.tmp.name, "needles.db"))
        self.addCleanup(db.close)
        st = WorldState()
        for n in names:
            st.data["characters"][n] = {"description": "x", "attitude_toward_player": "中立",
                                        "affinity": 40, "location": random.choice(places)}
        for t in range(1, 301):
            before = json.loads(json.dumps(st.data))
            st.data["meta"]["turn"] = t
            st.data["location"] = {"name": random.choice(places), "description": ""}
            if t in needles:
                who, nar, _, _ = needles[t]
                inp = f"和{who}说话"
            else:
                who = random.choice(names)
                act = random.choice(["闲聊了几句近况", "一起吃了顿饭", "讨论了修炼心得",
                                     "擦肩而过点头致意", "商量了坊市的行情"])
                nar, inp = f"你与{who}{act}，没有什么特别的事发生。", f"找{who}"
            db.record_turn(t, "action", nar, player_input=inp, update={}, state=st.data, state_before=before)
        hits = 0
        for t, (who, _nar, q, key) in needles.items():
            for query in (q, f"问{who}：" + q, q[-4:]):
                got = db.recall_for_turn(query, st.data, 301)
                hits += any(key in x["text"] for x in got)
        self.assertGreaterEqual(hits, 8)
        for weird in ('他说"你好"', "A-B: 测试", "100% 确定", "（）、。"):
            db.recall_for_turn(weird, st.data, 301)   # 不应抛异常

    def test_alias_recall_mentions_conflicts_and_cleaning(self):
        st = self._state()
        world_state.apply_update(st, {"characters": {"陆青鸢": {"aliases": ["阿鸢", "", 12, "很长的非法名字超过八个字", "青鸢", "阿鸢"]}}})
        self.assertEqual(st.data["characters"]["陆青鸢"]["aliases"], ["阿鸢", "青鸢"])
        with storage.SaveDB(config.db_path_for("")) as db:
            db.record_turn(1, "action", "陆青鸢送你一枚玄龟玉佩。", state=st.data)
            db.record_turn(2, "action", "阿鸢提醒你别弄丢了。", state=st.data)
            self.assertEqual(len(db.entity_profile("陆师姐")["recent_turns"]), 2)
            hits = db.recall_for_turn("问陆师姐当年的事", st.data, 10)
            self.assertTrue(any("玄龟玉佩" in x["text"] for x in hits))
            world_state.apply_update(st, {"characters": {"陆明": {"aliases": ["阿鸢"]}}})
            db.record_turn(3, "action", "陆明来了。", state=st.data)
            self.assertIsNone(db.entity_profile("陆师姐"))
            self.assertEqual(db.entity_profile("阿鸢")["name"], "陆青鸢")
            self.assertEqual(db.entity_profile("青鸢")["name"], "陆青鸢")

    def test_disappearing_facts_and_same_named_entities(self):
        st = self._state()
        st.data["characters"] = {"青山": {"status": "受伤", "location": "青山"}, "路人": {"status": "安好"}}
        st.data["location"] = {"name": "青山", "description": "山峰"}
        st.data["inventory"] = [{"name": "药丸", "quantity": 1}]
        st.data["plot_flags"] = {"约定": True}
        before = copy.deepcopy(st.data)
        st.data["characters"]["青山"]["status"] = ""
        del st.data["characters"]["路人"]
        st.data["inventory"] = []
        st.data["plot_flags"] = {}
        with storage.SaveDB(config.db_path_for("")) as db:
            db.record_turn(12, "action", "青山吃掉药丸。", state_before=before, state=st.data)
            item = db.entity_profile("药丸", kind="item")
            self.assertEqual([f["value"] for f in item["facts"] if f["valid_to_turn"] is None], ["0"])
            for name, attr in [("青山", "状态"), ("路人", "状态"), ("约定", "值")]:
                facts = db.entity_profile(name)["facts"]
                self.assertFalse(any(f["attribute"] == attr and f["valid_to_turn"] is None for f in facts))
            self.assertEqual(db.entity_profile("青山", "location")["kind"], "location")
            self.assertEqual(db.entity_profile("青山", "character")["kind"], "character")
            self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM facts WHERE valid_to_turn < valid_from_turn").fetchone()[0], 0)

    def test_v3_entity_unique_migration_preserves_links(self):
        import sqlite3
        path = config.db_path_for("")
        conn = sqlite3.connect(path)
        conn.executescript(storage.SCHEMA.replace("name TEXT NOT NULL,", "name TEXT NOT NULL UNIQUE,").replace(
            "last_turn_id INTEGER,\n  UNIQUE(kind, name)", "last_turn_id INTEGER"))
        conn.execute("INSERT INTO entities(id,kind,name) VALUES(7,'character','青山')")
        conn.execute("INSERT INTO aliases(alias,entity_id) VALUES('阿山',7)")
        conn.execute("INSERT INTO facts(entity_id,attribute,value,valid_from_turn,valid_to_turn) VALUES(7,'状态','好',12,11)")
        conn.commit()
        conn.close()
        with storage.SaveDB(path) as db:
            self.assertEqual(db.entity_profile("阿山")["name"], "青山")
            self.assertEqual(db.get_meta("schema_version"), "4")
            self.assertNotEqual(db._entity_id("location", "青山", 1), 7)
            self.assertEqual(db.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            fact = db.entity_profile("阿山")["facts"][0]
            self.assertLessEqual(fact["valid_from_turn"], fact["valid_to_turn"])

    def test_exact_replay_fifty_mixed_game_turns(self):
        st = self._state()
        st.data["world"]["background"] = "不变的世界设定。" * 500
        game = Game(st, memory.Memory(), Replies(), rules=[])
        full_bytes = 0
        with patch.dict(os.environ, {"WANJIE_STORAGE": "sqlite"}), patch.object(random, "random", return_value=0.99):
            for turn in range(1, 51):
                if turn % 3 == 0:
                    game.llm = Replies(("切磋", {"combat": {"enemy_name": "教习", "enemy_power": 100, "action": "fight", "lethal": False}}),
                                       ("切磋结束。", {}))
                    game.run_turn("切磋")
                elif turn % 3 == 1:
                    game.llm = Replies(("尝试", {"judgment": {"type": "action"}}), ("尝试结束。", {"inventory_add": [{"name": "石头", "quantity": 1}]}))
                    game.run_turn("尝试")
                else:
                    game.llm = Replies(("休养一旬。", {"turn_event": "休养"}))
                    game.run_block("休养", 10)
                persisted = json.loads(Path(self.tmp.name, "state.json").read_text())
                full_bytes += len(storage._dump(persisted).encode())
                with storage.SaveDB(config.db_path_for("")) as db:
                    self.assertEqual(db.load_state(), persisted)
            with storage.SaveDB(config.db_path_for("")) as db:
                # 最后一轮正好有快照，删除它后再核对，防止只验证快照而没测增量。
                db.conn.execute("DELETE FROM snapshots WHERE turn_id=(SELECT MAX(id) FROM turns)")
                self.assertEqual(db.load_state(), persisted)
                cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(state_changes)")}
                self.assertNotIn("state_after_json", cols)
                saved_bytes = db.conn.execute("SELECT SUM(length(CAST(diff_json AS BLOB))) FROM state_changes").fetchone()[0]
                saved_bytes += db.conn.execute("SELECT SUM(length(CAST(state_json AS BLOB))) FROM snapshots").fetchone()[0]
                self.assertLess(saved_bytes, full_bytes * 0.4)

    def test_full_state_column_migrates_to_deltas(self):
        path = config.db_path_for("")
        st = self._state().data
        with storage.SaveDB(path) as db:
            db.record_turn(1, "action", "开始", state=st)
            after = copy.deepcopy(st)
            after["player"]["life_status"] = "dead"
            after["player"]["attributes"]["hp"] = 0
            del after["plot_flags"]
            db.record_turn(2, "action", "结束", state=after)
            db.conn.execute("ALTER TABLE state_changes ADD COLUMN state_after_json TEXT")
            db.conn.execute("UPDATE state_changes SET state_after_json=? WHERE turn_id=1", (storage._dump(st),))
            db.conn.execute("UPDATE state_changes SET state_after_json=? WHERE turn_id=2", (storage._dump(after),))
        with storage.SaveDB(path) as db:
            self.assertEqual(db.load_state(), after)
            self.assertEqual(db.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertNotIn("state_after_json", {r["name"] for r in db.conn.execute("PRAGMA table_info(state_changes)")})

    def test_attribute_event_and_direct_growth_caps(self):
        st = self._state()
        world_state.apply_update(st, {"attribute_events": [{"attribute": "魅力", "delta": 1000000}]})
        self.assertEqual(st.data["player"]["attributes"]["魅力"], 20)
        for _ in range(20):
            world_state.apply_update(st, {"attribute_events": [{"attribute": "魅力", "delta": 20}] * 10})
        self.assertEqual(st.data["player"]["attributes"]["魅力"], 200)
        fresh = self._state()
        world_state.apply_update(fresh, {"player_attributes": {"魅力": 1000000, "自创属性": 1000000},
                                         "attribute_events": [{"attribute": "魅力", "delta": 1000000}] * 10})
        self.assertEqual(fresh.data["player"]["attributes"]["魅力"], 20)
        self.assertEqual(fresh.data["player"]["attributes"]["自创属性"], 20)
        fresh.data["player"]["attributes"]["自创属性"] = 9999
        world_state.apply_update(fresh, {"attribute_events": [{"attribute": "自创属性", "delta": 20}]})
        self.assertEqual(fresh.data["player"]["attributes"]["自创属性"], 10000)
        world_state.apply_update(fresh, {"attribute_events": [{"attribute": "福缘", "delta": -1000000}]})
        self.assertEqual(fresh.data["player"]["attributes"]["福缘"], 0)

    def test_judgment_uses_previous_turn_attributes(self):
        st = self._state()
        st.data["player"]["attributes"]["魅力"] = 10
        game = Game(st, memory.Memory(), Replies(("说服成功。", {})), rules=[])
        game._state_before = copy.deepcopy(st.data)
        st.data["player"]["attributes"]["魅力"] = 200
        with patch.object(judgment, "resolve_judgment", return_value=(True, 0.5)) as resolve:
            game._handle_judgment("尝试说服", {"judgment": {"type": "persuasion"}}, "说服")
        links = judgment.ATTRIBUTE_FACTOR_LINKS["persuasion"]
        for factor, attr in links.items():
            if attr == "魅力":
                self.assertEqual(resolve.call_args.args[1][factor], judgment.attribute_factor(10))

    def test_storage_failure_logs_and_recovers_with_gap_snapshot(self):
        game = Game(self._state(), memory.Memory(), Replies(("第一回合。", {}), ("第二回合。", {})), rules=[])
        with patch.dict(os.environ, {"WANJIE_STORAGE": "sqlite"}):
            with patch.object(storage, "append_turn", side_effect=OSError("模拟磁盘故障")):
                game.run_turn("前进")
            self.assertEqual(game.storage_errors, 1)
            self.assertEqual(world_state.WorldState.load().data["meta"]["turn"], 1)
            log = Path(self.tmp.name, "wanjie.log").read_text()
            self.assertIn("第 1 回", log)
            self.assertIn("模拟磁盘故障", log)
            self.assertIn("Traceback", log)
            game.run_turn("继续")
        with storage.SaveDB(config.db_path_for("")) as db:
            self.assertEqual([r["kind"] for r in reversed(db.list_turns())], ["gap", "action"])
            self.assertEqual(db.get_meta("last_successful_turn"), "2")
            self.assertEqual(db.load_state(), world_state.WorldState.load().data)
            self.assertEqual(db.stats()["snapshots"], 1)

    def test_entity_write_failure_rolls_back_entire_database_turn(self):
        import sqlite3
        with storage.SaveDB(config.db_path_for("")) as db:
            with patch.object(db, "_sync_entities", side_effect=sqlite3.OperationalError("模拟实体写失败")):
                with self.assertRaises(sqlite3.OperationalError):
                    db.record_turn(1, "action", "失败记录", state=self._state().data)
            self.assertEqual(db.stats(), {"turns": 0, "snapshots": 0, "rolls": 0})
            self.assertFalse(db.recall("失败记录")["turns"])
            self.assertIsNone(db.get_meta("last_successful_turn"))

    def test_recall_failure_is_logged_without_stopping_game(self):
        game = Game(self._state(), memory.Memory(), Replies(("继续。", {})), rules=[])
        with storage.SaveDB(config.db_path_for("")) as db:
            db.record_turn(0, "action", "开头", state=game.state.data)
        with patch.dict(os.environ, {"WANJIE_STORAGE": "sqlite"}), patch.object(storage.SaveDB, "recall_for_turn", side_effect=RuntimeError("模拟检索故障")):
            game.run_turn("继续")
        self.assertEqual(game.storage_errors, 0)
        self.assertIn("读取历史记忆失败", Path(self.tmp.name, "wanjie.log").read_text())

    def test_migration_keeps_real_turn_numbers_and_continues(self):
        st = self._state()
        st.data["meta"]["turn"] = 23
        st.data["characters"] = {"陆青鸢": {"status": "安好"}}
        st.save()
        mem = memory.Memory()
        mem.recent = [{"role": role, "content": f"第 {n} 回{role}"}
                      for n in (21, 22, 23) for role in ("user", "assistant")]
        mem.recent.append({"role": "user", "content": "未完成的行动"})
        mem.save()
        path, count = storage.migrate_from_json(save_dir=self.tmp.name)
        self.assertEqual(count, 3)
        with storage.SaveDB(path) as db:
            self.assertEqual([r["turn_no"] for r in reversed(db.list_turns())], [21, 22, 23])
            self.assertEqual(db.load_state(), st.data)
            self.assertTrue(all(f["valid_from_turn"] == 23 for f in db.entity_profile("陆青鸢")["facts"]))
        game = Game(st, mem, Replies(("第24回", {})), rules=[])
        with patch.dict(os.environ, {"WANJIE_STORAGE": "sqlite"}):
            game.run_turn("继续")
        with storage.SaveDB(path) as db:
            self.assertEqual(db.list_turns()[0]["turn_no"], 24)
            self.assertFalse(any(r["kind"] == "gap" for r in db.list_turns()))
            self.assertEqual(db.load_state(), world_state.WorldState.load().data)

    def test_migration_without_history_keeps_current_turn(self):
        st = self._state()
        st.data["meta"]["turn"] = 23
        st.save()
        path, count = storage.migrate_from_json()
        with storage.SaveDB(path) as db:
            self.assertEqual(count, 1)
            self.assertEqual(db.list_turns()[0]["turn_no"], 23)
            self.assertEqual(db.load_state(), st.data)

    def test_fts_recovery_indexes_old_turns_and_literal_queries(self):
        path = config.db_path_for("")
        with storage.SaveDB(path) as db:
            db.record_turn(1, "action", '他说"你好"，报价100%，代号A-B:。')
            db.conn.execute("DROP TABLE turns_fts")
        with storage.SaveDB(path) as db:
            db.record_turn(2, "action", '他说"你好"，新的见面。')
            self.assertEqual({r["turn_no"] for r in db.recall('他说"你好"')["turns"]}, {1, 2})
            for query in ("100%", "A-B:"):
                self.assertEqual(db.recall(query)["turns"][0]["turn_no"], 1)
            db.fts_available = False
            self.assertEqual(db.recall("100%")["turns"][0]["turn_no"], 1)
            self.assertEqual(db.recall("不存在%")["turns"], [])

    def test_recall_only_closed_facts_with_time_range(self):
        st = self._state()
        st.data["characters"] = {"赵执事": {"attitude_toward_player": "敌视"}}
        with storage.SaveDB(config.db_path_for("")) as db:
            db.record_turn(1, "action", "一次相遇。", state=st.data)
            before = copy.deepcopy(st.data)
            st.data["characters"]["赵执事"]["attitude_toward_player"] = "友善"
            db.record_turn(10, "action", "冰释前嫌。", state=st.data, state_before=before)
            hits = db.recall_for_turn("赵执事以前的态度", st.data, 20)
            self.assertTrue(any("第 1~9 回" in h["text"] and "敌视" in h["text"] for h in hits))
            self.assertFalse(any("友善" in h["text"] for h in hits))

    def _state(self, hp=100, flags=None):
        st = world_state.WorldState()
        st.apply_template({"world": {"background": "存储测试"},
                           "player": {"name": "林舟", "attributes": {"hp": hp, "max_hp": 100}}})
        if flags:
            st.data["plot_flags"].update(flags)
        return st

    def test_record_replay_and_stats(self):
        st = self._state()
        db = storage.SaveDB(config.db_path_for(""))
        try:
            db.record_turn(1, "action", "第一回合", player_input="出发", update={}, state=st.data)
            st.data["player"]["attributes"]["hp"] = 50
            st.data["plot_flags"]["x"] = True
            db.record_turn(2, "action", "第二回合", player_input="受伤",
                           update={"player_attributes": {"hp": 50}, "plot_flags": {"x": True}},
                           state=st.data,
                           roll={"kind": "judgment", "payload": {"probability": 0.3, "success": False}})
            self.assertEqual(db.stats()["turns"], 2)
            self.assertEqual(len(db.list_turns()), 2)
            self.assertEqual(db.list_rolls()[0]["kind"], "judgment")
            rebuilt = db.load_state()
            self.assertEqual(rebuilt, st.data)  # 最后写入的完整状态可精确还原
            self.assertEqual(rebuilt["player"]["attributes"]["hp"], 50)
            self.assertTrue(rebuilt["plot_flags"]["x"])
        finally:
            db.close()

    def test_snapshot_every_ten_turns(self):
        st = self._state()
        db = storage.SaveDB(config.db_path_for(""))
        try:
            for n in range(1, 26):
                db.record_turn(n, "action", f"回合{n}", update={}, state=st.data)
            self.assertEqual(db.stats()["snapshots"], 3)  # 第 1、10、20 回合
        finally:
            db.close()

    def test_migrate_from_json(self):
        st = self._state(hp=77, flags={"已迁移": True})
        st.data["meta"]["turn"] = 3
        st.save()
        mem = memory.Memory()
        mem.recent = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答"}]
        mem.save()
        db_path, turns = storage.migrate_from_json("")
        self.assertIsNotNone(db_path)
        self.assertGreaterEqual(turns, 1)
        db = storage.SaveDB(db_path)
        try:
            self.assertGreaterEqual(db.stats()["turns"], 1)
            rebuilt = db.load_state()
            self.assertEqual(rebuilt["player"]["attributes"]["hp"], 77)
            self.assertTrue(rebuilt["plot_flags"].get("已迁移"))
        finally:
            db.close()


    def test_turn_summary_is_stored_and_searchable(self):
        """每轮剧情概括入库，并优先作为检索片段（正文里没有关键词也能召回）。"""
        db = storage.SaveDB(config.db_path_for(""))
        try:
            db.record_turn(1, "action", "正文里没有那个词，只有动作描写。",
                           player_input="四处看看", summary="青灯照壁的暗号被刻在墙上")
            row = db.list_turns(1)[0]
            self.assertEqual(row["summary"], "青灯照壁的暗号被刻在墙上")
            hits = db.recall("青灯照壁")["turns"]
            self.assertTrue(hits)
            self.assertIn("青灯照壁", hits[0]["excerpt"])
        finally:
            db.close()

    def test_old_db_is_migrated_with_summary_and_fts(self):
        """阶段 1 之前建的旧库（无 summary 列、FTS 两列）打开时自动升级。"""
        import sqlite3
        path = config.db_path_for("")
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE turns(id INTEGER PRIMARY KEY AUTOINCREMENT, turn_no INTEGER NOT NULL, "
            "kind TEXT NOT NULL, player_input TEXT, narrative TEXT NOT NULL, location TEXT, "
            "game_time TEXT, calendar_days INTEGER, created_at TEXT NOT NULL);"
            "INSERT INTO turns(turn_no, kind, narrative, created_at) VALUES(1,'action','旧原文','x');"
            "CREATE VIRTUAL TABLE turns_fts USING fts5(player_input, narrative, tokenize='trigram');"
            "INSERT INTO turns_fts(rowid, player_input, narrative) VALUES(1,'','旧原文');")
        conn.commit()
        conn.close()
        db = storage.SaveDB(path)
        try:
            cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(turns)").fetchall()}
            self.assertIn("summary", cols)
            fts_cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(turns_fts)").fetchall()}
            self.assertIn("summary", fts_cols)
            db.record_turn(2, "action", "新的一回合", summary="新的暗号：月照寒潭")
            self.assertTrue(db.recall("月照寒潭")["turns"])
        finally:
            db.close()

    def test_game_dual_write_is_optional_and_safe(self):
        # 默认关闭：不产生 .db
        game = Game(world_state.WorldState(), memory.Memory(),
                    Replies(("你好。", {"turn_event": "t"})), rules=[])
        game.run_turn("打个招呼")
        self.assertFalse(os.path.exists(config.db_path_for("")))
        # WANJIE_STORAGE=sqlite：每回合双写
        with patch.dict(os.environ, {"WANJIE_STORAGE": "sqlite"}):
            game = Game(world_state.WorldState(), memory.Memory(),
                        Replies(("你好。", {"turn_event": "t", "turn_summary": "林舟打了个招呼"})), rules=[])
            game.run_turn("打个招呼")
        self.assertTrue(os.path.exists(config.db_path_for("")))
        db = storage.SaveDB(config.db_path_for(""))
        try:
            self.assertEqual(db.stats()["turns"], 1)
            self.assertEqual(db.list_turns()[0]["narrative"], "你好。")
            self.assertEqual(db.list_turns()[0]["summary"], "林舟打了个招呼")
            rebuilt = db.load_state()
            self.assertEqual(rebuilt["meta"]["turn"], 1)  # 回合号等代码侧改动也要能还原
        finally:
            db.close()


    def test_facts_mentions_and_profile(self):
        """路线图 §2.3：从前后状态对比生成实体、事实（旧事实封口）与提及。"""
        before = self._state(hp=100)
        before.data["location"] = {"name": "集市", "description": "热闹"}
        before.data["characters"] = {"甲": {"description": "摊主", "attitude_toward_player": "中立",
                                            "location": "集市", "status": "安好", "affinity": 40, "met_turn": 0}}
        before.data["inventory"] = [{"name": "灵石", "quantity": 5}]
        after = copy.deepcopy(before.data)
        after["characters"]["甲"].update({"attitude_toward_player": "友善", "affinity": 55})
        after["inventory"] = [{"name": "灵石", "quantity": 3}]
        db = storage.SaveDB(config.db_path_for(""))
        try:
            db.record_turn(1, "action", "你和甲在集市聊了几句，甲收了灵石。",
                           player_input="找甲买符", update={}, state=after, state_before=before.data)
            profile = db.entity_profile("甲")
            self.assertIsNotNone(profile)
            current = {f["attribute"]: f["value"] for f in profile["facts"] if f["valid_to_turn"] is None}
            self.assertEqual(current["态度"], "友善")
            self.assertEqual(current["所在"], "集市")
            attitudes = [f for f in profile["facts"] if f["attribute"] == "态度"]
            self.assertEqual(len(attitudes), 2)  # 旧「中立」被封口 + 新「友善」
            self.assertTrue(any(f["valid_to_turn"] is not None for f in attitudes))
            self.assertEqual(current["持有"] if "持有" in current else None, None)  # 持有在物品实体上
            item = db.entity_profile("灵石")
            self.assertEqual({f["attribute"]: f["value"] for f in item["facts"]
                              if f["valid_to_turn"] is None}["持有"], "3")
            self.assertTrue(profile["recent_turns"])
            self.assertEqual(profile["recent_turns"][0]["role"], "present")
        finally:
            db.close()

    def test_recall_exact_and_fallback(self):
        db = storage.SaveDB(config.db_path_for(""))
        try:
            db.record_turn(1, "action", "老槐树下埋着一只旧木匣。", player_input="去老槐树看看")
            db.record_turn(2, "action", "师妹在旁边轻声提醒你小心。", player_input="问师妹")
            # 3 字以上走 FTS trigram；1~2 字回退 LIKE
            self.assertTrue(db.recall("老槐树")["turns"])
            self.assertTrue(db.recall("师妹")["turns"])
            self.assertFalse(db.recall("不存在的词组")["turns"])
            self.assertIsNone(db.recall(entity="不存在的人")["entity"])
        finally:
            db.close()

    def test_migration_seeds_entities(self):
        st = self._state(hp=80)
        st.data["location"] = {"name": "村口", "description": "土路"}
        st.data["characters"] = {"老王": {"description": "猎户", "attitude_toward_player": "友善", "affinity": 55}}
        st.data["inventory"] = [{"name": "灵石", "quantity": 2}]
        st.save()
        db_path, _ = storage.migrate_from_json("")
        db = storage.SaveDB(db_path)
        try:
            self.assertIsNotNone(db.entity_profile("老王"))
            self.assertIsNotNone(db.entity_profile("灵石"))
            self.assertIsNotNone(db.entity_profile("村口"))
        finally:
            db.close()


    def test_recall_for_turn_ranks_excludes_and_limits(self):
        """路线图 §2.5：每轮召回排除最近几轮、按分数排序、限制条数。"""
        before = self._state()
        after1 = copy.deepcopy(before.data)
        after1["location"] = {"name": "集市", "description": "热闹"}
        after1["characters"] = {"老王": {"description": "掌柜", "attitude_toward_player": "中立",
                                        "location": "集市", "affinity": 40, "met_turn": 0}}
        db = storage.SaveDB(config.db_path_for(""))
        try:
            db.record_turn(1, "action", "你在集市遇见老王，他收下了你的灵石。",
                           player_input="去集市找老王",
                           update={"relationship_events": [{"subject": "老王", "event": "赠礼"}],
                                   "characters": {"老王": {"description": "掌柜"}}},
                           state=after1, state_before=before.data)
            db.record_turn(2, "action", "雨下了一整夜，无事发生。", player_input="睡觉", update={}, state=after1,
                           state_before=after1)
            hits = db.recall_for_turn("再去找老王问问", after1, current_turn=6)
            self.assertTrue(hits)
            self.assertFalse(any("描述：" in str(h["text"]) for h in hits))  # 空值事实不进召回
            self.assertIn("老王", str(hits[0]["text"]))
            self.assertLessEqual(len(db.recall_for_turn("老王", after1, current_turn=6, limit=1)), 1)
            # current_turn=3 时，最近 3 轮（第 1、2 轮）都被排除
            self.assertEqual(db.recall_for_turn("老王", after1, current_turn=3), [])
            for item in hits:
                self.assertTrue({"turn_no", "text", "score"} <= set(item))
        finally:
            db.close()

    def test_turn_messages_place_recalled_before_recent(self):
        state = world_state.WorldState()
        state.apply_template({"world": {"background": "测试"}})
        mem = memory.Memory()
        mem.recent = [{"role": "user", "content": "最近一句玩家话"},
                      {"role": "assistant", "content": "最近一句主持话"}]
        msg = prompts.build_turn_messages(state, mem, "现在行动",
                                          recalled=[{"turn_no": 3, "text": "很久以前的暗号：青灯照壁"}])
        content = msg[0]["content"]
        self.assertLess(content.index("【当前世界状态摘要】"), content.index("【相关往事"))
        self.assertLess(content.index("【相关往事"), content.index("【最近几轮对话】"))
        self.assertIn("[第 3 回]", content)
        self.assertIn("青灯照壁", content)
        # 没有召回时不出现该段落
        plain = prompts.build_turn_messages(state, mem, "现在行动")[0]["content"]
        self.assertNotIn("【相关往事", plain)
        # 长度预算
        block = prompts.build_recall_block([{"turn_no": i, "text": "很长的往事" * 40} for i in range(30)])
        self.assertLessEqual(len(block), prompts.RECALL_MAX_CHARS)

    def test_game_uses_recall_when_sqlite_enabled(self):
        before = self._state()
        after1 = copy.deepcopy(before.data)
        after1["location"] = {"name": "集市", "description": ""}
        after1["characters"] = {"老王": {"description": "掌柜", "attitude_toward_player": "友善",
                                        "location": "集市", "affinity": 55, "met_turn": 0}}
        db = storage.SaveDB(config.db_path_for(""))
        try:
            db.record_turn(1, "action", "你在集市遇见老王。", player_input="去集市",
                           update={}, state=after1, state_before=before.data)
        finally:
            db.close()
        st = world_state.WorldState(copy.deepcopy(after1))
        st.data["meta"]["turn"] = 6  # 让第 1 轮落在「最近 3 轮」之外
        game = Game(st, memory.Memory(), object(), rules=[])
        with patch.dict(os.environ, {"WANJIE_STORAGE": "sqlite"}):
            content = game._turn_messages("再去找老王问问")[0]["content"]
        self.assertIn("【相关往事", content)
        self.assertIn("老王", content)
        # 关闭存储时不召回
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WANJIE_STORAGE", None)
            plain = game._turn_messages("再去找老王问问")[0]["content"]
        self.assertNotIn("【相关往事", plain)



class ProfileReplies(Replies):
    def __init__(self, *replies, profiles=None):
        super().__init__(*replies)
        self.profiles = list(profiles or [])
        self.profile_calls = []

    def call_tool(self, messages, tool):
        self.profile_calls.append(copy.deepcopy(messages))
        reply = self.profiles.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return "", "build_character_profile", copy.deepcopy(reply)


class CharacterProfileTests(GameFixture):
    def test_profile_freeze_development_and_old_save(self):
        st = world_state.WorldState()
        st.data["characters"]["甲"] = {"trait": "谨慎", "profile": {
            "personality": "重承诺", "speech": "简短", "locked_turn": 0,
            "sample_lines": ["请进。", "稍等。"], "revisions": []}}
        world_state.apply_update(st, {"characters": {"甲": {"trait": "油滑", "appearances": 999,
            "profile": {"personality": "反复"}, "personality": "反复", "goal": "还钱", "mood": "着急"}}})
        char = st.data["characters"]["甲"]
        self.assertEqual(char["trait"], "谨慎")
        self.assertEqual(char["profile"]["personality"], "重承诺")
        self.assertEqual(char["profile"]["goal"], "还钱")
        self.assertNotIn("appearances", char)
        event = {"subject": "甲", "field": "personality", "new": "开始怀疑宗门", "reason": "弟弟病逝后，他不再相信宗门"}
        world_state.apply_update(st, {"character_development": [event]})
        self.assertEqual(char["profile"]["revisions"], [])
        st.data["meta"]["turn"] = 30
        world_state.apply_update(st, {"character_development": [dict(event, reason=""), event, dict(event, new="另一个性格")]})
        self.assertEqual(char["profile"]["personality"], "开始怀疑宗门")
        self.assertEqual(char["profile"]["revisions"][0]["old"], "重承诺")
        self.assertEqual(char["profile"]["revisions"][0]["turn"], 31)
        self.assertEqual(char["profile_pending"], "refresh_lines")
        self.assertEqual(char["profile"]["sample_lines"], [])
        st.data["meta"]["turn"] = 32
        world_state.apply_update(st, {"character_development": [dict(event, new="另一个性格")]})
        self.assertEqual(len(char["profile"]["revisions"]), 1)
        with patch.object(world_state, "PROFILE_LOCK", False):
            world_state.apply_update(st, {"character_development": [dict(event, new="另一个性格")]})
        self.assertEqual(len(char["profile"]["revisions"]), 2)

    def test_appearances_count_once_and_preserve_old_characters(self):
        game = self.game(Replies(("甲回应了阿甲。", {"characters": {"甲": {"status": "安好", "appearances": 999}}})))
        game.state.data["characters"] = {"甲": {"description": "路人", "aliases": ["阿甲"]}, "乙": {"description": "路人"}}
        game.state.save()
        game.state = world_state.WorldState.load()
        game.run_turn("找阿甲")
        self.assertEqual(game.state.data["characters"]["甲"]["appearances"], 1)
        self.assertNotIn("appearances", game.state.data["characters"]["乙"])

    def test_character_cards_budget_selection_and_privacy(self):
        st = world_state.WorldState()
        st.data["location"]["name"] = "丹堂"
        for i in range(6):
            st.data["characters"][f"角色{i}"] = {"location": "丹堂", "profile": {
                "identity": "身份"*20, "personality": "谨慎"*30, "speech": "说话简短"*15,
                "bottom_line": "不欺骗"*10, "cares": "病人"*20, "goal": "找药"*20,
                "sample_lines": ["先等一下。"*5, "药放这里。"*5]}, "memories": ["很久以前的事"*10]}
        summary = st.to_summary()
        self.assertEqual(summary.count("说话："), 4)
        self.assertEqual(summary.count("例句："), 4)
        self.assertIn("角色4", summary.split("【其他人物】")[1])
        for name, info in st.data["characters"].items():
            self.assertLessEqual(len(world_state.character_card(name, info, st.data)), 180)
        st.data["location"]["name"] = "坊市"
        st.data["characters"]["角色5"]["aliases"] = ["师姐"]
        summary = st.to_summary("问师姐", "角色4刚来过")
        relevant = summary.split("【其他人物】")[0]
        self.assertIn("角色5", relevant)
        self.assertIn("角色4", relevant)
        visible = st.to_player_summary()
        self.assertNotIn("说话简短", visible)
        self.assertNotIn("先等一下", visible)
        mem = memory.Memory(recent=[{"role": "assistant", "content": "角色4刚来过"}])
        self.assertIn("角色5", prompts.build_turn_messages(st, mem, "问师姐")[0]["content"])

    def test_old_character_summary_keeps_description_status_and_memories(self):
        st = world_state.WorldState()
        st.data["characters"] = {"甲": {"description": "猎户", "status": "受伤", "memories": ["答应给我药"]}}
        summary = st.to_summary()
        for word in ("猎户", "受伤", "答应给我药"):
            self.assertIn(word, summary)

    def test_profile_promotion_and_distinct_reference_and_sqlite(self):
        profile = {"personality": "重承诺", "speech": "简短直接", "cares": "家人", "bottom_line": "不骗人", "sample_lines": ["请进。", "药放这里。"]}
        llm = ProfileReplies(*[("甲来了。", {})]*3, profiles=[profile])
        game = self.game(llm)
        game.state.data["characters"] = {"甲": {"description": "猎户"}, "乙": {"profile": dict(profile, speech="爱开玩笑")}}
        with patch.object(config, "SAVE_DIR", self.tmp.name), patch.dict(os.environ, {"WANJIE_STORAGE": "sqlite"}):
            game.run_turn("找甲")
            game.run_turn("找甲")
            self.assertEqual(llm.profile_calls, [])
            game.run_turn("找甲")
            self.assertEqual(len(llm.profile_calls), 1)
            self.assertIn("爱开玩笑", str(llm.profile_calls[0]))
            self.assertEqual(game.state.data["characters"]["甲"]["profile"]["locked_turn"], 3)
            with storage.SaveDB(config.db_path_for("")) as db:
                self.assertEqual(db.load_state(), world_state.WorldState.load().data)
                self.assertTrue(any(f["attribute"] == "说话方式" for f in db.entity_profile("甲")["facts"]))

    def test_profile_queue_retry_limit_and_postcommit_failure(self):
        llm = ProfileReplies(*[("甲乙丙都在。", {})]*4, profiles=[{"personality": "谨慎"}, RuntimeError("模拟失败"), {}])
        game = self.game(llm)
        game.state.data["characters"] = {n: {"appearances": 3 if n == "甲" else 1, "profile_pending": "build"} for n in "甲乙丙"}
        with patch.object(config, "SAVE_DIR", self.tmp.name):
            for attempt in range(1,4):
                game.run_turn("找甲")
                self.assertEqual(game.state.data["characters"]["甲"]["profile_attempts"], attempt)
                self.assertEqual(world_state.WorldState.load().data["meta"]["turn"], attempt)
                self.assertEqual(len(llm.profile_calls), attempt)
            self.assertNotIn("profile_pending", game.state.data["characters"]["甲"])
            self.assertIn("profile_pending", game.state.data["characters"]["乙"])
            game.llm = Replies(("甲来了。", {}))
            game.run_turn("甲")
            self.assertNotIn("profile_pending", game.state.data["characters"]["甲"])

    def test_template_character_promotes_only_on_appearance(self):
        profile = {"personality": "谨慎", "speech": "直说", "sample_lines": ["请进。", "坐吧。"]}
        game = self.game(ProfileReplies(("门外无人。", {}), ("老王来了。", {}), profiles=[profile]))
        game.state.apply_template({"characters": {"老王": {"description": "猎户"}, "老李": {"description": "掌柜"}}})
        game.run_turn("看看门外")
        self.assertEqual(len(game.llm.profile_calls), 0)
        game.run_turn("继续等")
        self.assertEqual(len(game.llm.profile_calls), 1)
        self.assertIn("profile", game.state.data["characters"]["老王"])
        self.assertNotIn("profile", game.state.data["characters"]["老李"])

    def test_profile_refresh_changes_only_lines_and_limit_twenty(self):
        profile = {"personality": "谨慎", "speech": "直说", "sample_lines": [], "locked_turn": 1, "revisions": []}
        game = self.game(ProfileReplies(("甲来了。", {}), profiles=[dict(profile, speech="乱说", sample_lines=["请进。", "坐吧。"])]))
        game.state.data["characters"] = {str(i): {"profile": dict(profile)} for i in range(20)}
        game.state.data["characters"]["甲"] = {"profile": dict(profile), "profile_pending": "refresh_lines"}
        game.state.data["characters"]["路人"] = {"appearances": 9}
        game.run_turn("甲")
        self.assertEqual(game.state.data["characters"]["甲"]["profile"]["speech"], "直说")
        self.assertEqual(len(game.state.data["characters"]["甲"]["profile"]["sample_lines"]), 2)
        self.assertNotIn("profile_pending", game.state.data["characters"]["路人"])

    def test_prose_modes_and_metrics(self):
        import style_metrics as metrics
        st = world_state.WorldState()
        default = prompts.build_system_prompt(st, [])
        self.assertIn("【语言——朴素直白】", default)
        self.assertIn("掌柜摇头", default)
        self.assertIn("巷子里太安静", default)
        self.assertIn("你怎么才来", default)
        st.data["world"].pop("prose")
        self.assertEqual(default, prompts.build_system_prompt(st, []))
        st.data["world"]["prose"] = "文学"
        literary = prompts.build_system_prompt(st, [])
        self.assertNotIn("【语言——朴素直白】", literary)
        self.assertIn("底线：涉及性的角色一律是成年人；不出现未成年人的性内容。", literary)
        st.data["world"]["prose"] = "标准"
        standard = prompts.build_system_prompt(st, [])
        self.assertIn("不替人物解释内心", standard)
        self.assertNotIn("对照例子", standard)
        self.assertGreater(metrics.cliche_rate("她眸光微动，嘴角勾起一抹笑。"), 0)
        self.assertEqual(metrics.cliche_rate("你怎么才来？"), 0)
        self.assertEqual(metrics.avg_sentence_len("你好。再见！"), 2)
        self.assertEqual(metrics.dialogue_tag_rate('她眸光微动：「你好。」'), 1)
        self.assertEqual(metrics.dialogue_tag_rate('「她似乎走了。」'), 0)
        self.assertEqual(metrics.report([])["dialogue_tag_rate"], 0)

    def test_style_report_readonly_database_and_history_fallback(self):
        from scripts.style_report import read_narratives
        path = Path(self.tmp.name, "history.json")
        path.write_text(json.dumps({"recent": [{"role": "assistant", "content": "你好"}]}))
        self.assertEqual(read_narratives(save_dir=self.tmp.name), ["你好"])
        with storage.SaveDB(str(Path(self.tmp.name, "game.db"))) as db:
            db.record_turn(1, "action", "数据库叙事")
        self.assertEqual(read_narratives(save_dir=self.tmp.name), ["数据库叙事"])


if __name__ == "__main__":
    unittest.main()
