"""验证候选卡片换组、锁定和移除在真实页面状态中能够衔接。"""
from pathlib import Path
import random
import unittest

import character_builder as cb

try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None


class PoolLLM:
    def __init__(self):
        self.calls = []

    def call_tool(self, messages, tool, **kwargs):
        key = tool["function"]["parameters"]["required"][0]
        self.calls.append((key, messages))
        tiers = ["炫彩", "红", "红", "金", "金", "金"]
        return "", "generate_character_pool", {key: [
            {"name": "新" + key + str(i), "description": "测试候选",
             "tier": tiers[i] if i < 6 else "紫", "drawback": "测试代价" if i < 6 else ""}
            for i in range(cb.POOL_TARGET_COUNTS[key])
        ]}


@unittest.skipIf(AppTest is None, "页面检查需要安装Streamlit")
class CharacterPoolUITests(unittest.TestCase):
    def test_reroll_lock_hidden_selection_and_regeneration(self):
        rng_state = random.getstate()
        self.addCleanup(random.setstate, rng_state)
        random.seed(20261013)
        llm = PoolLLM()
        legacy = {key: [{"name": "旧" + key + str(i), "description": "旧候选", "tier": "蓝"}
                        for i in range(6)] for key in cb.POOL_TARGET_COUNTS}
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
        app.session_state["llm"] = llm
        app.session_state["_temp_slots_pruned"] = True  # 测试不清理真实 save/
        app.session_state["character_step"] = True
        app.session_state["template"] = {"world": {"background": "测试修仙世界"}}
        app.session_state["character_pool"] = legacy
        app.session_state["talent_hand"] = [legacy["talents"][0]] * 9
        app.run(timeout=15)
        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(all(len(app.session_state["character_pool"][key]) >= cb.MIN_POOL_SIZE
                            for key in cb.POOL_TARGET_COUNTS))
        hand = app.session_state["talent_hand"]
        self.assertEqual(len({item["name"] for item in hand}), 9)
        selected = [item["name"] for item in hand[:3]]
        for name in selected:
            app.button(key="sel_talent_" + name).click().run(timeout=15)
            self.assertFalse(app.exception, str(app.exception))
        locked = selected[0]
        app.button(key="lock_talent_" + locked).click().run(timeout=15)
        other_hands = {cat: [item["name"] for item in app.session_state[cat + "_hand"]]
                       for cat in ("physique", "gf")}
        removed = None
        for _ in range(10):
            old = {item["name"] for item in app.session_state["talent_hand"]}
            app.button(key="roll_talent").click().run(timeout=15)
            self.assertFalse(app.exception, str(app.exception))
            new = {item["name"] for item in app.session_state["talent_hand"]}
            self.assertGreaterEqual(len(new - old), 3)
            self.assertIn(locked, new)
            self.assertEqual(set(app.session_state["talent_sel"]), set(selected))
            outside = [name for name in selected if name not in new]
            if outside:
                removed = outside[0]
                app.button(key="remove_talent_" + removed).click().run(timeout=15)
                break
        self.assertIsNotNone(removed)
        self.assertNotIn(removed, app.session_state["talent_sel"])
        for cat, names in other_hands.items():
            self.assertEqual(names, [item["name"] for item in app.session_state[cat + "_hand"]])
        self.assertFalse(llm.calls)  # 本地换组不重复请求模型。
        next(button for button in app.button if button.label == "重新生成候选池").click().run(timeout=15)
        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(len(llm.calls), 3)
        self.assertFalse(app.session_state.filtered_state.get("talent_sel"))
        self.assertFalse(app.session_state.filtered_state.get("talent_lock"))
        for _, messages in llm.calls:
            self.assertIn("避免复用上一批这些名字", messages[-1]["content"])

    def test_auto_build_fills_a_full_build_without_drawbacks(self):
        llm = PoolLLM()
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
        app.session_state["llm"] = llm
        app.session_state["_temp_slots_pruned"] = True  # 测试不清理真实 save/
        app.session_state["character_step"] = True
        app.session_state["template"] = {"world": {"background": "测试修仙世界"}}
        app.run(timeout=15)
        self.assertFalse(app.exception, str(app.exception))
        pool = app.session_state["character_pool"]
        self.assertFalse(any(item.get("drawback") for key in cb.POOL_TARGET_COUNTS for item in pool[key]))
        app.button(key="auto_build").click().run(timeout=15)
        self.assertFalse(app.exception, str(app.exception))
        talents = app.session_state["talent_sel"]
        self.assertTrue(2 <= len(talents) <= cb.MAX_TALENTS)
        self.assertEqual(len(set(talents)), len(talents))
        self.assertIn(app.session_state["physique_sel"], {x["name"] for x in pool["physiques"]})
        self.assertEqual(len(app.session_state["gf_sel"]), 1)
        self.assertEqual(len(llm.calls), 3)  # 一键天命只在本地随机，不额外请求模型


if __name__ == "__main__":
    unittest.main()
