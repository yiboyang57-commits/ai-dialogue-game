"""对话历史管理：近期窗口 + 压缩缓冲。

上下文不会无限增长：
- recent：只保留最近 KEEP_RECENT_ENTRIES 条完整对话（最近几轮）。
- buffer：累积待压缩的轮次，达到 COMPRESS_EVERY_TURNS 轮后交给模型压缩成摘要，
  摘要写回 world_state.narrative_summary，buffer 清空。
"""
import json
import os

from config import SAVE_DIR, KEEP_RECENT_ENTRIES, COMPRESS_EVERY_TURNS, save_slot_suffix


class Memory:
    def __init__(self, recent=None, buffer=None, turns_since_compression=0, save_key=""):
        self.recent = recent or []            # [{"role": "user"/"assistant", "content": str}]
        self.buffer = buffer or []            # 待压缩对话（同结构）
        self.turns_since_compression = turns_since_compression
        self.save_key = save_key or ""

    # ---- 追加 ----
    def add_user(self, text):
        self.recent.append({"role": "user", "content": text})
        self.buffer.append({"role": "user", "content": text})
        self._trim()

    def add_assistant(self, text):
        self.recent.append({"role": "assistant", "content": text})
        self.buffer.append({"role": "assistant", "content": text})
        self._trim()
        self.turns_since_compression += 1  # 一轮 = 主持人回应一次

    def _trim(self):
        if len(self.recent) > KEEP_RECENT_ENTRIES:
            self.recent = self.recent[-KEEP_RECENT_ENTRIES:]

    # ---- 压缩判定与格式化 ----
    def needs_compression(self):
        return self.turns_since_compression >= COMPRESS_EVERY_TURNS

    def format_buffer(self):
        lines = []
        for t in self.buffer:
            role = "玩家" if t["role"] == "user" else "主持人"
            lines.append(f"{role}: {t['content']}")
        return "\n".join(lines)

    def reset_after_compression(self):
        self.buffer = []
        self.turns_since_compression = 0

    # ---- 持久化 ----
    def to_dict(self):
        return {
            "recent": self.recent,
            "buffer": self.buffer,
            "turns_since_compression": self.turns_since_compression,
        }

    @classmethod
    def from_dict(cls, d, save_key=""):
        return cls(
            recent=d.get("recent") or [],
            buffer=d.get("buffer") or [],
            turns_since_compression=d.get("turns_since_compression", 0),
            save_key=save_key,
        )

    @property
    def history_path(self):
        return os.path.join(SAVE_DIR, "history" + save_slot_suffix(self.save_key) + ".json")

    def save(self):
        os.makedirs(SAVE_DIR, exist_ok=True)
        with open(self.history_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, save_key=""):
        key = save_key or ""
        path = os.path.join(SAVE_DIR, "history" + save_slot_suffix(key) + ".json")
        if not os.path.exists(path):
            return cls(save_key=key)
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f), save_key=key)
