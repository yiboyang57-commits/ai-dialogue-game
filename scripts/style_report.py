#!/usr/bin/env python3
"""只读统计存档叙事：优先 SQLite，缺库时回退最近对话 JSON。"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import config
from style_metrics import report


def read_narratives(slot="", save_dir=None):
    base = Path(save_dir or config.SAVE_DIR)
    suffix = config.save_slot_suffix(slot)
    db_path = base / ("game" + suffix + ".db")
    if db_path.exists():
        with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            return [r[0] for r in conn.execute("SELECT narrative FROM turns WHERE kind!='gap' ORDER BY id") if r[0]]
    path = base / ("history" + suffix + ".json")
    if not path.exists():
        raise FileNotFoundError("找不到该槽的历史库或对话记录。")
    data = json.loads(path.read_text(encoding="utf-8"))
    return [m["content"] for m in data.get("recent", []) if isinstance(m, dict)
            and m.get("role") == "assistant" and isinstance(m.get("content"), str)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slot", default="")
    args = parser.parse_args()
    try:
        print(json.dumps(report(read_narratives(args.slot)), ensure_ascii=False, indent=2))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print("无法统计：" + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
