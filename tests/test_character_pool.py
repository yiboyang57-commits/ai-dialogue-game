"""候选池不足、重复或锁定时，随机不能退化为只换顺序。"""
import random
import unittest
from collections import Counter
from unittest.mock import patch

import character_builder as cb


def candidates(prefix, n):
    tiers = ["炫彩", "红", "红", "金", "金", "金"]
    return [{"name": f"{prefix}{i}", "tier": tiers[i] if i < len(tiers) else "紫", "description": "测试效果"} for i in range(n)]


class CategoryLLM:
    def __init__(self, failed=None, partial=False):
        self.failed = failed
        self.partial = partial
        self.calls = []

    def call_tool(self, messages, tool, **kwargs):
        params = tool["function"]["parameters"]
        key = params["required"][0]
        self.calls.append((key, messages, params, kwargs))
        if key == self.failed:
            raise RuntimeError("模拟该类生成失败")
        values = candidates(key, 2 if self.partial else cb.POOL_TARGET_COUNTS[key])
        return "", "generate_character_pool", {key: values}


class CharacterPoolTests(unittest.TestCase):
    def test_short_and_empty_pools_are_filled_with_distinct_candidates(self):
        pool = cb.prepare_pool({"talents": [{"name": "唯一候选", "tier": "蓝"}]})
        all_names = []
        for key in cb.POOL_TARGET_COUNTS:
            self.assertGreaterEqual(len(pool[key]), cb.MIN_POOL_SIZE)
            counts = Counter(item["tier"] for item in pool[key])
            for tier, minimum in cb._POOL_FLOOR.items():
                self.assertGreaterEqual(counts[tier], minimum)
            all_names += [item["name"].casefold() for item in pool[key]]
        self.assertEqual(len(all_names), len(set(all_names)))

    def test_duplicates_and_malformed_entries_are_removed(self):
        pool = cb._normalize_pool({
            "talents": [{"name": " 同名 ", "tier": "蓝"}, {"name": "同名", "tier": "紫"}, {"name": 3}, "坏条目"],
            "physiques": [{"name": "同名"}, {"name": "另一体质", "description": 123, "drawback": []}],
            "golden_fingers": "不是数组",
        })
        self.assertEqual([x["name"] for x in pool["talents"]], ["同名"])
        self.assertEqual([x["name"] for x in pool["physiques"]], ["另一体质"])
        self.assertEqual(pool["golden_fingers"], [])

    def test_each_reroll_brings_new_candidates_and_keeps_lock(self):
        items = candidates("天赋", 18)
        rng = random.Random(20261013)
        with patch.object(cb.random, "sample", rng.sample), patch.object(cb.random, "shuffle", rng.shuffle):
            hand = cb.sample_hand(items)
            locked = hand[0]["name"]
            for _ in range(200):
                previous = hand
                hand = cb.sample_hand(items, locked=locked, previous=previous)
                names = {x["name"] for x in hand}
                self.assertEqual(len(names), cb.HAND_SIZE)
                self.assertIn(locked, names)
                self.assertGreaterEqual(len(names - {x["name"] for x in previous}), 3)

    def test_tiny_input_never_duplicates_or_breaks_sampling(self):
        duplicate = {"name": "唯一", "tier": "蓝"}
        self.assertEqual(cb.sample_hand([duplicate] * 9, locked="唯一", previous=[duplicate]), [duplicate])
        self.assertEqual(cb.sample_hand([], locked="不存在"), [])
        self.assertEqual(len(cb.sample_hand(candidates("候选", 3), locked="不存在")), 3)

    def test_each_api_request_generates_only_one_category(self):
        llm = CategoryLLM()
        pool = cb.generate_pool(llm, {"world": {"background": "测试世界"}})
        self.assertEqual({c[0] for c in llm.calls}, set(cb.POOL_TARGET_COUNTS))
        for key, _, params, kwargs in llm.calls:
            self.assertEqual(list(params["properties"]), [key])
            self.assertEqual(kwargs["max_tokens"], 4096)
        self.assertEqual(set(pool["_meta"]["sources"].values()), {"ai"})

    def test_one_failed_category_does_not_discard_other_generated_options(self):
        llm = CategoryLLM(failed="physiques")
        pool = cb.generate_pool(llm, {"world": {"background": "测试世界"}})
        self.assertTrue(any(x["name"] == "talents0" for x in pool["talents"]))
        self.assertTrue(any(x["name"] == "golden_fingers0" for x in pool["golden_fingers"]))
        self.assertEqual(pool["_meta"]["sources"]["physiques"], "local")
        self.assertGreaterEqual(len(pool["physiques"]), cb.MIN_POOL_SIZE)

    def test_partial_success_still_has_room_to_reroll(self):
        pool = cb.generate_pool(CategoryLLM(partial=True), {})
        for key in cb.POOL_TARGET_COUNTS:
            self.assertGreaterEqual(len(pool[key]), cb.MIN_POOL_SIZE)
            self.assertEqual(pool["_meta"]["sources"][key], "mixed")

    def test_regeneration_tells_model_which_old_names_to_avoid(self):
        llm = CategoryLLM()
        previous = {key: [{"name": "上一批" + key}] for key in cb.POOL_TARGET_COUNTS}
        cb.generate_pool(llm, {}, previous_pool=previous)
        for key, messages, _, _ in llm.calls:
            self.assertIn("上一批" + key, messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
