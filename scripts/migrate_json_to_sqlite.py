#!/usr/bin/env python3
"""把 save/ 下的 JSON 存档迁移为 SQLite 事件库（路线图阶段 1 的迁移脚本）。

用法：
    .venv/bin/python scripts/migrate_json_to_sqlite.py            # 迁移默认槽
    .venv/bin/python scripts/migrate_json_to_sqlite.py --slot alice
    .venv/bin/python scripts/migrate_json_to_sqlite.py --all      # 迁移所有槽

迁移产物：save/game_<槽>.db（turns / state_changes / rolls / snapshots）。
JSON 仍是主存储；迁移后的 .db 用于历史、审计与「用快照 + 增量回放」恢复状态。
"""
import argparse
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config   # noqa: E402
import storage  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="把万界 JSON 存档迁移成 SQLite 事件库")
    parser.add_argument("--slot", default="", help="存档槽名（默认空槽）")
    parser.add_argument("--all", action="store_true", help="迁移所有已有存档槽")
    args = parser.parse_args()

    keys = [""]
    if args.all:
        keys = [""] + [s["key"] for s in config.list_save_slots()]
    elif args.slot:
        keys = [args.slot]

    migrated = 0
    for key in keys:
        db_path, turn_count = storage.migrate_from_json(key)
        if db_path is None:
            print("跳过（没有可迁移的 JSON 存档）：" + (key or "默认槽"))
            continue
        migrated += 1
        print(f"迁移完成：{key or '默认槽'} → {db_path}（{turn_count} 回合）")

    if migrated:
        print("\n提示：JSON 仍是主存储。开启 SQLite 双写可用：")
        print("  WANJIE_STORAGE=sqlite .venv/bin/python -m streamlit run app.py")
    return 0 if migrated else 1


if __name__ == "__main__":
    raise SystemExit(main())
