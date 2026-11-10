"""SQLite 存储层（路线图阶段 1 的第一步）。

定位：**不替换 JSON 主存储**。JSON 仍是唯一事实来源，SQLite 作为可选的历史/审计/恢复层：
- 一个存档 = 一个 `save/game_<槽>.db`；
- 每回合追加一条 `turns` + 一条 `state_changes`（清洗后的增量）；
- 每 10 回合写一份完整 `snapshots`；
- 战斗/判定的掷骰记录写入 `rolls`，方便复盘「这一下为什么成败」；
- 「当前状态」= 最近快照 + 之后逐回合的 state_changes 用 `apply_update` 叠回去。

开关：环境变量 `WANJIE_STORAGE=sqlite` 时才开启双写（默认关闭，行为与旧版完全一致）。
写库失败一律静默（由 game 侧兜底），绝不影响游戏本身。
"""
import json
import os
import sqlite3
import time

SCHEMA_VERSION = 1
SNAPSHOT_EVERY_TURNS = 10   # 每 N 回合写一份完整状态快照

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn_no INTEGER NOT NULL,
  kind TEXT NOT NULL,                      -- opening / action / block / migrated
  player_input TEXT,
  narrative TEXT NOT NULL,
  location TEXT,
  game_time TEXT,
  calendar_days INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state_changes (
  turn_id INTEGER PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
  update_json TEXT NOT NULL,
  state_after_json TEXT,                   -- 提交后的完整状态（含代码侧改动：回合号/日历/死亡）
  diff_json TEXT
);
CREATE TABLE IF NOT EXISTS rolls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn_id INTEGER REFERENCES turns(id) ON DELETE CASCADE,
  kind TEXT,                               -- combat / judgment
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
  turn_id INTEGER PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
  state_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_turn_no ON turns(turn_no);
CREATE INDEX IF NOT EXISTS idx_turns_kind ON turns(kind);
CREATE INDEX IF NOT EXISTS idx_rolls_turn ON rolls(turn_id);
"""


def enabled():
    """是否开启 SQLite 双写（默认关闭，JSON 仍是主存储）。"""
    return (os.environ.get("WANJIE_STORAGE") or "").strip().lower() == "sqlite"


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _dump(value):
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


class SaveDB:
    """一个存档对应的 SQLite 事件库。"""

    def __init__(self, path):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.set_meta("schema_version", str(SCHEMA_VERSION))
        self.conn.commit()

    # ---- 基础 ----
    def set_meta(self, key, value):
        self.conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def get_meta(self, key, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def close(self):
        try:
            self.conn.commit()
        finally:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ---- 写入：一回合一个事务 ----
    def record_turn(self, turn_no, kind, narrative, player_input=None, update=None,
                    state=None, roll=None, snapshot_every=SNAPSHOT_EVERY_TURNS):
        """追加一回合。state 非空时按 `snapshot_every` 决定是否写快照（首回合必写）。"""
        turn_no = int(turn_no or 0)
        location = (state or {}).get("location", {}).get("name") if isinstance(state, dict) else None
        game_time = (state or {}).get("time") if isinstance(state, dict) else None
        calendar_days = None
        if isinstance(state, dict):
            calendar_days = (state.get("calendar") or {}).get("days")
        with self.conn:  # 事务：任何一步失败整轮回滚
            cur = self.conn.execute(
                "INSERT INTO turns(turn_no, kind, player_input, narrative, location, game_time, "
                "calendar_days, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (turn_no, str(kind or "action"), player_input, str(narrative or ""),
                 location, game_time, calendar_days, _now()))
            turn_id = cur.lastrowid
            self.conn.execute(
                "INSERT INTO state_changes(turn_id, update_json, state_after_json, diff_json) "
                "VALUES(?,?,?,?)",
                (turn_id, _dump(update), _dump(state) if isinstance(state, dict) else None, None))
            if roll:
                self.conn.execute(
                    "INSERT INTO rolls(turn_id, kind, payload_json, created_at) VALUES(?,?,?,?)",
                    (turn_id, str(roll.get("kind") or "judgment"), _dump(roll.get("payload")), _now()))
            if isinstance(state, dict) and (turn_id == 1 or turn_no % max(1, snapshot_every) == 0):
                self.conn.execute("INSERT OR REPLACE INTO snapshots(turn_id, state_json) VALUES(?,?)",
                                  (turn_id, _dump(state)))
        return turn_id

    def record_snapshot(self, turn_id, state):
        """给已有回合补一份完整快照（迁移/手动存档用）。"""
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO snapshots(turn_id, state_json) VALUES(?,?)",
                              (int(turn_id), _dump(state)))

    # ---- 读取 ----
    def list_turns(self, limit=50):
        rows = self.conn.execute(
            "SELECT id, turn_no, kind, player_input, narrative, location, game_time, "
            "calendar_days, created_at FROM turns ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def latest_snapshot(self):
        row = self.conn.execute(
            "SELECT turn_id, state_json FROM snapshots ORDER BY turn_id DESC LIMIT 1").fetchone()
        if not row:
            return None, None
        return row["turn_id"], json.loads(row["state_json"])

    def load_state(self, save_key=""):
        """重建 state dict（没有数据返回 None）。

        优先用最后一回合的 `state_after_json`（完整、精确，包含回合号/日历/死亡等代码侧改动）；
        老库没有该列时退回「最近快照 + 之后逐回合增量」回放。
        """
        import world_state
        row = self.conn.execute(
            "SELECT state_after_json FROM state_changes ORDER BY turn_id DESC LIMIT 1").fetchone()
        if row and row["state_after_json"]:
            return json.loads(row["state_after_json"])
        snap_id, data = self.latest_snapshot()
        if data is None:
            return None
        state = world_state.WorldState(data, save_key=save_key)
        rows = self.conn.execute(
            "SELECT update_json FROM state_changes WHERE turn_id > ? ORDER BY turn_id",
            (snap_id,)).fetchall()
        for row in rows:
            try:
                update = json.loads(row["update_json"])
            except json.JSONDecodeError:
                continue
            world_state.apply_update(state, update)
        return state.data

    def list_rolls(self, limit=20):
        rows = self.conn.execute(
            "SELECT id, turn_id, kind, payload_json, created_at FROM rolls "
            "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def stats(self):
        def count(table):
            return self.conn.execute("SELECT COUNT(*) AS n FROM " + table).fetchone()["n"]
        return {"turns": count("turns"), "snapshots": count("snapshots"), "rolls": count("rolls")}


def append_turn(db_path, **kwargs):
    """打开→记录→关闭。供 game 双写使用（写库失败由调用方吞掉，不影响游戏）。"""
    db = SaveDB(db_path)
    try:
        return db.record_turn(**kwargs)
    finally:
        db.close()


def migrate_from_json(save_key="", save_dir=None):
    """把一个 JSON 存档迁移成 .db（路线图阶段 1 的迁移脚本核心）。

    迁移后：`turns` 里是 history 还能找到的最近几轮原文（更早的只剩摘要），
    末尾写一份完整快照——因此 `load_state()` 能还原出与 JSON 完全一致的当前状态。
    返回 (db_path, 写入回合数)。
    """
    import config
    suffix = config.save_slot_suffix(save_key)
    base = save_dir or config.SAVE_DIR
    state_path = os.path.join(base, "state" + suffix + ".json")
    if not os.path.exists(state_path):
        return None, 0
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state_data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None, 0
    hist_path = os.path.join(base, "history" + suffix + ".json")
    try:
        with open(hist_path, "r", encoding="utf-8") as f:
            rounds = list((json.load(f) or {}).get("recent") or [])
    except (json.JSONDecodeError, OSError):
        rounds = []

    db_path = config.db_path_for(save_key)
    db = SaveDB(db_path)
    try:
        db.set_meta("source", "json")
        db.set_meta("save_key", save_key or "")
        db.set_meta("migrated_at", _now())
        db.conn.commit()
        turn_no = 0
        pending_user = None
        for msg in rounds:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "user":
                pending_user = content
            elif role == "assistant" and pending_user is not None:
                turn_no += 1
                db.record_turn(turn_no, "migrated", content, player_input=pending_user, update={})
                pending_user = None
        if pending_user is not None:
            turn_no += 1
            db.record_turn(turn_no, "migrated", "", player_input=pending_user, update={})
        if turn_no == 0:
            turn_no = 1
            db.record_turn(turn_no, "migrated",
                           state_data.get("narrative_summary") or "（迁移的存档，没有可用的对话原文）",
                           update={})
        # 最后一回合写成完整快照，保证 load_state 与 JSON 一致
        last_id = db.conn.execute("SELECT id FROM turns ORDER BY id DESC LIMIT 1").fetchone()["id"]
        db.record_snapshot(last_id, state_data)
    finally:
        db.close()
    return db_path, turn_no
