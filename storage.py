"""SQLite 存储层（路线图阶段 1 的第一步）。

定位：**不替换 JSON 主存储**。JSON 仍是唯一事实来源，SQLite 作为可选的历史/审计/恢复层：
- 一个存档 = 一个 `save/game_<槽>.db`；
- 每回合追加一条 `turns` + 一条 `state_changes`（清洗后的增量）；
- 每 10 回合写一份完整 `snapshots`；
- 战斗/判定的掷骰记录写入 `rolls`，方便复盘「这一下为什么成败」；
- 「当前状态」= 最近快照 + 之后逐回合的确定性 JSON 差异（含代码侧结算）。

开关：环境变量 `WANJIE_STORAGE=sqlite` 时才开启双写（默认关闭，行为与旧版完全一致）。
写库失败由 game 侧记录日志和计数，绝不影响已保存的游戏本身。
"""
import copy
import json
import logging
import sys
import os
import re
import sqlite3
import time

STORAGE_LOG_NAME = "wanjie.log"

AUTO_TITLES = ("师姐", "师兄", "掌柜", "执事", "长老", "前辈", "道友")

QUERY_MAX_TERMS = 8
QUERY_COMMON_RATIO = 0.20
RECALL_TEXT_CHARS = 110
RECALL_MATCH_WEIGHT = 2.0
RECALL_NAMED_WEIGHT = 1.0
RECALL_CONTEXT_WEIGHT = 0.3
RECALL_IMPORTANCE_WEIGHT = 0.5
RECALL_RECENCY_WEIGHT = 0.1
RECALL_DECAY = 0.98

SCHEMA_VERSION = 4
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
  summary TEXT,                            -- 模型给的一两句话剧情概括（检索/回忆用）
  location TEXT,
  game_time TEXT,
  calendar_days INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state_changes (
  turn_id INTEGER PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
  update_json TEXT NOT NULL,
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
CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,                      -- character / location / item / plot
  name TEXT NOT NULL,
  first_turn_id INTEGER,
  last_turn_id INTEGER,
  UNIQUE(kind, name)
);
CREATE TABLE IF NOT EXISTS aliases (
  alias TEXT PRIMARY KEY,
  entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS mentions (
  turn_id INTEGER REFERENCES turns(id) ON DELETE CASCADE,
  entity_id INTEGER REFERENCES entities(id) ON DELETE CASCADE,
  role TEXT,                               -- present / target / mentioned
  PRIMARY KEY (turn_id, entity_id)
);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  attribute TEXT NOT NULL,                 -- 态度 / 好感度 / 所在 / 状态 / 持有 / 标记
  value TEXT NOT NULL,
  valid_from_turn INTEGER NOT NULL,
  valid_to_turn INTEGER,                   -- NULL = 现在仍然成立
  source_turn_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_turns_turn_no ON turns(turn_no);
CREATE INDEX IF NOT EXISTS idx_turns_kind ON turns(kind);
CREATE INDEX IF NOT EXISTS idx_rolls_turn ON rolls(turn_id);
CREATE INDEX IF NOT EXISTS idx_facts_entity ON facts(entity_id, attribute, valid_to_turn);
CREATE INDEX IF NOT EXISTS idx_mentions_entity ON mentions(entity_id);
"""


def enabled():
    """是否开启 SQLite 双写（默认关闭，JSON 仍是主存储）。"""
    return (os.environ.get("WANJIE_STORAGE") or "").strip().lower() == "sqlite"


def log_exception(message, turn_no=0):
    """用标准库记录异常堆栈；日志本身写失败也不能破坏 JSON 主流程。"""
    import config
    logger = logging.getLogger("wanjie.storage")
    try:
        os.makedirs(config.SAVE_DIR, exist_ok=True)
        handler = logging.FileHandler(os.path.join(config.SAVE_DIR, STORAGE_LOG_NAME), encoding="utf-8")
        try:
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            record = logger.makeRecord(logger.name, logging.ERROR, __file__, 0,
                                       "第 %s 回：%s", (turn_no, message), sys.exc_info())
            handler.handle(record)
        finally:
            handler.close()
    except Exception:
        logger.exception("第 %s 回：%s（历史库日志也无法写入）", turn_no, message)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _dump(value):
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _query_terms(player_input, known_names):
    """提取实体称呼和汉字滑窗；频率筛选由持有数据库的调用方完成。"""
    text = str(player_input or "")
    names = sorted({n for n in known_names if n and n in text}, key=lambda n: (-len(n), n))
    rest = text
    for name in names:
        rest = rest.replace(name, " ")
    rest = re.sub(r"多少|什么|当年|当时|那个|这个|帮我|问问|的|了|我|你|他|她|是|在|吗|呢|吧|问|找", " ", rest)
    terms = list(names)
    for segment in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", rest):
        if not re.fullmatch(r"[\u4e00-\u9fff]+", segment):
            terms.append(segment)
            continue
        for width in range(4, 1, -1):
            terms.extend(segment[i:i + width] for i in range(len(segment) - width + 1))
    return list(dict.fromkeys(terms))


def _fts_phrase(text):
    return '"' + str(text).replace('"', '""') + '"'


def _state_diff(before, after, path=()):
    """记录已结算状态的实际差异；列表原样替换，字典递归，支持键删除。"""
    if isinstance(before, dict) and isinstance(after, dict):
        ops = [{"path": list(path + (key,)), "remove": True} for key in before if key not in after]
        for key, value in after.items():
            if key not in before:
                ops.append({"path": list(path + (key,)), "value": value})
            else:
                ops.extend(_state_diff(before[key], value, path + (key,)))
        return ops
    if before != after or type(before) is not type(after):
        return [{"path": list(path), "value": after}]
    return []


def _apply_diff(data, ops):
    """只回放已发生的代码结果，不重新掷骰、不重跑成长钳制。"""
    for op in ops:
        path = op["path"]
        if not path:
            data = copy.deepcopy(op["value"])
            continue
        target = data
        for key in path[:-1]:
            target = target[key]
        if op.get("remove"):
            target.pop(path[-1], None)
        else:
            target[path[-1]] = copy.deepcopy(op["value"])
    return data


class SaveDB:
    """一个存档对应的 SQLite 事件库。"""

    def __init__(self, path):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=FULL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript(SCHEMA)
            self.fts_available = self._try_create_fts()
            self._migrate_schema()
            if self.fts_available:
                # 旧环境曾退回 LIKE 时可能没有索引；补齐旧回合，避免只搜索到新写入的回合。
                self.conn.execute(
                    "INSERT INTO turns_fts(rowid,player_input,narrative,summary) "
                    "SELECT t.id,COALESCE(t.player_input,''),t.narrative,COALESCE(t.summary,'') FROM turns t "
                    "WHERE NOT EXISTS (SELECT 1 FROM turns_fts f WHERE f.rowid=t.id)")
            self.set_meta("schema_version", str(SCHEMA_VERSION))
            self.conn.commit()
        except Exception:
            self.conn.close()
            raise

    def _migrate_schema(self):
        """老库升级：补 summary 列；FTS 旧版只有两列时重建并回填。"""
        # 旧版 name 全局唯一；重建实体表，保留 id，让事实/提及/别名外键原样有效。
        unique_columns = []
        for index in self.conn.execute("PRAGMA index_list(entities)"):
            if index["unique"]:
                unique_columns.append([r["name"] for r in self.conn.execute(
                    'PRAGMA index_info("' + index["name"].replace('"', '""') + '")')])
        if ["name"] in unique_columns:
            self.conn.commit()
            self.conn.execute("PRAGMA foreign_keys=OFF")
            try:
                with self.conn:
                    self.conn.execute("CREATE TABLE entities_v4 (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                                      "kind TEXT NOT NULL, name TEXT NOT NULL, first_turn_id INTEGER, "
                                      "last_turn_id INTEGER, UNIQUE(kind,name))")
                    self.conn.execute("INSERT INTO entities_v4 SELECT id,kind,name,first_turn_id,last_turn_id FROM entities")
                    self.conn.execute("DROP TABLE entities")
                    self.conn.execute("ALTER TABLE entities_v4 RENAME TO entities")
            finally:
                self.conn.execute("PRAGMA foreign_keys=ON")
        change_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(state_changes)")}
        if "state_after_json" in change_cols:
            self._migrate_state_deltas()
        # 修正旧库曾写出的反向有效期，不能把无效区间继续传播到召回。
        self.conn.execute("UPDATE facts SET valid_from_turn=valid_to_turn "
                          "WHERE valid_to_turn < valid_from_turn")
        alias_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(aliases)")}
        if "source" not in alias_cols:
            self.conn.execute("ALTER TABLE aliases ADD COLUMN source TEXT NOT NULL DEFAULT 'declared'")
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(turns)").fetchall()}
        if "summary" not in cols:
            self.conn.execute("ALTER TABLE turns ADD COLUMN summary TEXT")
        if self.fts_available:
            fts_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(turns_fts)").fetchall()}
            if "summary" not in fts_cols:
                try:
                    self.conn.execute("DROP TABLE IF EXISTS turns_fts")
                    self.conn.execute(
                        "CREATE VIRTUAL TABLE turns_fts USING fts5("
                        "player_input, narrative, summary, tokenize='trigram')")
                    self.conn.execute(
                        "INSERT INTO turns_fts(rowid, player_input, narrative, summary) "
                        "SELECT id, COALESCE(player_input,''), narrative, COALESCE(summary,'') FROM turns")
                except sqlite3.Error:
                    log_exception("重建全文索引失败，使用 LIKE 检索")
                    self.fts_available = False

    def _migrate_state_deltas(self):
        """旧库全量状态转换为精确差异；保留已有快照并移除重复的全量列。"""
        previous = None
        with self.conn:
            rows = self.conn.execute("SELECT c.*, s.state_json FROM state_changes c "
                                     "LEFT JOIN snapshots s ON s.turn_id=c.turn_id ORDER BY c.turn_id").fetchall()
            for row in rows:
                full = row["state_after_json"] or row["state_json"]
                if full:
                    current = json.loads(full)
                    if previous is None:
                        self.conn.execute("INSERT OR REPLACE INTO snapshots(turn_id,state_json) VALUES(?,?)",
                                          (row["turn_id"], _dump(current)))
                    self.conn.execute("UPDATE state_changes SET diff_json=? WHERE turn_id=?",
                                      (_dump(_state_diff(previous or {}, current)), row["turn_id"]))
                    previous = current
                elif previous is not None:
                    import world_state
                    previous = world_state.apply_update(world_state.WorldState(previous), json.loads(row["update_json"])).data
            self.conn.execute("CREATE TABLE state_changes_delta (turn_id INTEGER PRIMARY KEY REFERENCES turns(id) "
                              "ON DELETE CASCADE, update_json TEXT NOT NULL, diff_json TEXT)")
            self.conn.execute("INSERT INTO state_changes_delta SELECT turn_id,update_json,diff_json FROM state_changes")
            self.conn.execute("DROP TABLE state_changes")
            self.conn.execute("ALTER TABLE state_changes_delta RENAME TO state_changes")

    def _try_create_fts(self):
        """SQLite >= 3.34 才有 trigram 分词器；建不成就退回 LIKE 检索。"""
        if sqlite3.sqlite_version_info < (3, 34, 0):
            return False
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5("
                "player_input, narrative, summary, tokenize='trigram')")
            return True
        except sqlite3.Error:
            log_exception("全文索引不可用，使用 LIKE 检索")
            return False

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
        if exc_type is not None:
            self.conn.rollback()
        self.close()

    # ---- 写入：一回合一个事务 ----
    def record_turn(self, turn_no, kind, narrative, player_input=None, update=None,
                    state=None, roll=None, snapshot_every=SNAPSHOT_EVERY_TURNS,
                    state_before=None, summary=None):
        """追加一回合。state 非空时按 `snapshot_every` 决定是否写快照（首回合必写）。

        state_before 用于对比出「这一回合变了什么」，生成 entities / facts / mentions。
        """
        turn_no = int(turn_no or 0)
        location = (state or {}).get("location", {}).get("name") if isinstance(state, dict) else None
        game_time = (state or {}).get("time") if isinstance(state, dict) else None
        calendar_days = None
        if isinstance(state, dict):
            calendar_days = (state.get("calendar") or {}).get("days")
        with self.conn:  # 事务：任何一步失败整轮回滚
            previous = self.load_state() if isinstance(state, dict) else None
            cur = self.conn.execute(
                "INSERT INTO turns(turn_no, kind, player_input, narrative, summary, location, "
                "game_time, calendar_days, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (turn_no, str(kind or "action"), player_input, str(narrative or ""),
                 str(summary or "").strip()[:200] or None,
                 location, game_time, calendar_days, _now()))
            turn_id = cur.lastrowid
            self.conn.execute(
                "INSERT INTO state_changes(turn_id, update_json, diff_json) VALUES(?,?,?)",
                (turn_id, _dump(update), _dump(_state_diff(previous or {}, state)) if isinstance(state, dict) else None))
            if roll:
                self.conn.execute(
                    "INSERT INTO rolls(turn_id, kind, payload_json, created_at) VALUES(?,?,?,?)",
                    (turn_id, str(roll.get("kind") or "judgment"), _dump(roll.get("payload")), _now()))
            if isinstance(state, dict) and (previous is None or kind == "gap" or turn_no % max(1, snapshot_every) == 0):
                self.conn.execute("INSERT OR REPLACE INTO snapshots(turn_id, state_json) VALUES(?,?)",
                                  (turn_id, _dump(state)))
            if self.fts_available:
                self.conn.execute(
                    "INSERT INTO turns_fts(rowid, player_input, narrative, summary) VALUES(?,?,?,?)",
                    (turn_id, player_input or "", str(narrative or ""), str(summary or "")))
            if isinstance(state, dict):
                self._sync_entities(turn_id, turn_no, state_before, state,
                                    player_input=player_input, narrative=narrative)
            if kind != "gap":
                self.set_meta("last_successful_turn", turn_no)
        return turn_id

    # ---- 实体 / 事实 / 提及（路线图 §2.3） ----
    def _entity_id(self, kind, name, turn_id):
        name = str(name or "").strip()
        if not name:
            return None
        row = self.conn.execute("SELECT id FROM entities WHERE kind=? AND name=?", (kind, name,)).fetchone()
        if row:
            self.conn.execute(
                "UPDATE entities SET last_turn_id=? WHERE id=? AND (last_turn_id IS NULL OR last_turn_id < ?)",
                (turn_id, row["id"], turn_id))
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO entities(kind, name, first_turn_id, last_turn_id) VALUES(?,?,?,?)",
            (kind, name, turn_id, turn_id))
        return cur.lastrowid

    def _facts_of(self, entity_id):
        rows = self.conn.execute(
            "SELECT attribute, value FROM facts WHERE entity_id=? AND valid_to_turn IS NULL",
            (entity_id,)).fetchall()
        return {r["attribute"]: r["value"] for r in rows}

    def _set_facts(self, entity_id, turn_id, turn_no, wanted, source_turn=None):
        """把 wanted={属性: 值} 与现存事实对比：变化则封口旧事实并插入新事实。"""
        if entity_id is None:
            return
        current = self._facts_of(entity_id)
        wanted = {k: v for k, v in wanted.items() if v is not None and str(v).strip()}
        for attr in current.keys() - wanted.keys():
            self.conn.execute("UPDATE facts SET valid_to_turn=MAX(valid_from_turn, ?) "
                              "WHERE entity_id=? AND attribute=? AND valid_to_turn IS NULL",
                              (turn_no, entity_id, attr))
        for attr, value in wanted.items():
            value = str(value)
            if current.get(attr) == value:
                continue
            if attr in current:
                self.conn.execute(
                    "UPDATE facts SET valid_to_turn=MAX(valid_from_turn, ?) WHERE entity_id=? AND attribute=? "
                    "AND valid_to_turn IS NULL", (turn_no - 1, entity_id, attr))
            self.conn.execute(
                "INSERT INTO facts(entity_id, attribute, value, valid_from_turn, valid_to_turn, source_turn_id) "
                "VALUES(?,?,?,?,NULL,?)", (entity_id, attr, value, turn_no, source_turn or turn_id))

    def _add_mention(self, turn_id, entity_id, role):
        if entity_id is None:
            return
        rank = {"mentioned": 1, "target": 2, "present": 3}
        row = self.conn.execute(
            "SELECT role FROM mentions WHERE turn_id=? AND entity_id=?", (turn_id, entity_id)).fetchone()
        if row and rank.get(row["role"], 0) >= rank.get(role, 0):
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO mentions(turn_id, entity_id, role) VALUES(?,?,?)",
            (turn_id, entity_id, role))

    def _apply_state_entities(self, turn_id, turn_no, state):
        """把某一状态快照里的地点/人物/物品/剧情标记写成实体与事实（不开新的事务）。"""
        if not isinstance(state, dict):
            return
        loc_info = state.get("location") or {}
        loc = loc_info.get("name")
        if loc:
            eid = self._entity_id("location", loc, turn_id)
            self._set_facts(eid, turn_id, turn_no, {"描述": loc_info.get("description", "")})
        for name, info in (state.get("characters") or {}).items():
            if not isinstance(info, dict):
                continue
            eid = self._entity_id("character", name, turn_id)
            wanted = {}
            if info.get("attitude_toward_player"):
                wanted["态度"] = info["attitude_toward_player"]
            if isinstance(info.get("affinity"), (int, float)):
                wanted["好感度"] = int(info["affinity"])
            if info.get("location"):
                wanted["所在"] = info["location"]
            if info.get("status"):
                wanted["状态"] = info["status"]
            profile = info.get("profile") if isinstance(info.get("profile"), dict) else {}
            for key, label in (("personality", "性格"), ("speech", "说话方式"), ("cares", "在意"), ("bottom_line", "底线")):
                if profile.get(key):
                    wanted[label] = profile[key]
            self._set_facts(eid, turn_id, turn_no, wanted)
        for item in (state.get("inventory") or []):
            if not isinstance(item, dict) or not item.get("name"):
                continue
            eid = self._entity_id("item", item["name"], turn_id)
            self._set_facts(eid, turn_id, turn_no, {"持有": item.get("quantity", 1)})
        for key, value in (state.get("plot_flags") or {}).items():
            eid = self._entity_id("plot", key, turn_id)
            self._set_facts(eid, turn_id, turn_no, {"值": value})

    def _sync_aliases(self, state):
        """声明别名先到先得；自动称呼冲突时撤销，避免后来同姓角色被认错。"""
        rows = self.conn.execute("SELECT id, kind, name FROM entities ORDER BY id").fetchall()
        canonical = {r["name"] for r in rows}
        chars = [r for r in rows if r["kind"] == "character"]
        self.conn.execute("DELETE FROM aliases WHERE source='auto'")
        for row in chars:
            info = (state.get("characters") or {}).get(row["name"], {})
            aliases = info.get("aliases", []) if isinstance(info, dict) else []
            if not isinstance(aliases, list):
                continue
            for alias in aliases[:3]:
                if not isinstance(alias, str) or not 1 <= len(alias.strip()) <= 8:
                    continue
                alias = alias.strip()
                if alias in canonical:
                    continue
                self.conn.execute("INSERT OR IGNORE INTO aliases(alias, entity_id, source) VALUES(?,?,'declared')",
                                  (alias, row["id"]))
        candidates = {}
        for row in chars:
            name = row["name"]
            if len(name) < 2:
                continue
            for alias in [name[1:]] + [name[0] + title for title in AUTO_TITLES]:
                candidates.setdefault(alias, set()).add(row["id"])
        for alias, ids in candidates.items():
            if len(ids) == 1 and alias not in canonical:
                self.conn.execute("INSERT OR IGNORE INTO aliases(alias, entity_id, source) VALUES(?,?,'auto')",
                                  (alias, next(iter(ids))))

    def _sync_entities(self, turn_id, turn_no, before, after, player_input="", narrative=""):
        """从本回合前后状态里同步实体、事实与提及。只做确定性逻辑，不调用模型。

        先按"回合前"写一遍事实、再按"回合后"写一遍：变化的属性会留下封口的历史事实
        （例如「态度=中立」在第 N 回合结束、换成「态度=友善」）。
        """
        before = before if isinstance(before, dict) else None
        after = after if isinstance(after, dict) else {}
        if before:
            self._apply_state_entities(turn_id, max(0, turn_no - 1), before)
        self._apply_state_entities(turn_id, turn_no, after)
        # 整个实体离开状态时也要封口；地点离开视野不等于地点本身消失。
        for kind, old, new in (
                ("character", (before or {}).get("characters") or {}, after.get("characters") or {}),
                ("plot", (before or {}).get("plot_flags") or {}, after.get("plot_flags") or {}),
                ("item", {i["name"]: i for i in (before or {}).get("inventory", []) if isinstance(i, dict) and i.get("name")},
                 {i["name"]: i for i in after.get("inventory", []) if isinstance(i, dict) and i.get("name")})):
            for name in old.keys() - new.keys():
                eid = self._entity_id(kind, name, turn_id)
                self._set_facts(eid, turn_id, turn_no, {})
                if kind == "item":
                    self._set_facts(eid, turn_id, turn_no, {"持有": 0})
        self._sync_aliases(after)
        # 提及：叙事里按已知名字精确匹配；在场/被行动对象优先于仅提及
        if not isinstance(after, dict):
            return
        text = " ".join(filter(None, [str(player_input or ""), str(narrative or "")]))
        player_loc = (after.get("location") or {}).get("name") or ""
        chars_before = (before or {}).get("characters") or {}
        for name, info in (after.get("characters") or {}).items():
            if not isinstance(info, dict) or not name:
                continue
            eid = self._entity_id("character", name, turn_id)
            aliases = [r["alias"] for r in self.conn.execute("SELECT alias FROM aliases WHERE entity_id=?", (eid,))]
            if not any(n in text for n in [name] + aliases):
                continue
            role = "mentioned"
            if player_loc and (info.get("location") or "") and (
                    player_loc in info["location"] or info["location"] in player_loc):
                role = "present"
            elif name not in chars_before or (chars_before.get(name) or {}) != info:
                role = "target"
            self._add_mention(turn_id, eid, role)
        for item in (after.get("inventory") or []):
            if isinstance(item, dict) and item.get("name") and item["name"] in text:
                self._add_mention(turn_id, self._entity_id("item", item["name"], turn_id), "mentioned")

    # ---- 检索（路线图 §2.5 第一层 + 全文兜底） ----
    def entity_profile(self, name, kind=None):
        """某人/某地/某物的当前事实 + 历史变化 + 最近出场回合。"""
        name = str(name or "").strip()
        if not name:
            return None
        row = self.conn.execute("SELECT id, kind, name, first_turn_id, last_turn_id FROM entities "
                                "WHERE name=? AND (? IS NULL OR kind=?) ORDER BY id LIMIT 1",
                                (name, kind, kind)).fetchone()
        if not row:
            row = self.conn.execute(
                "SELECT e.id, e.kind, e.name, e.first_turn_id, e.last_turn_id FROM entities e "
                "JOIN aliases a ON a.entity_id=e.id WHERE a.alias=? AND (? IS NULL OR e.kind=?)", (name, kind, kind)).fetchone()
        if not row:
            return None
        facts = [dict(r) for r in self.conn.execute(
            "SELECT attribute, value, valid_from_turn, valid_to_turn FROM facts "
            "WHERE entity_id=? ORDER BY id", (row["id"],)).fetchall()]
        turns = [dict(r) for r in self.conn.execute(
            "SELECT t.turn_no, m.role, "
            "COALESCE(NULLIF(t.summary,''), substr(t.narrative, 1, 120)) AS excerpt FROM mentions m "
            "JOIN turns t ON t.id=m.turn_id WHERE m.entity_id=? ORDER BY t.id DESC LIMIT 5",
            (row["id"],)).fetchall()]
        return {"name": row["name"], "kind": row["kind"], "facts": facts, "recent_turns": turns}

    def _like_search(self, query, limit):
        pattern = "%" + str(query).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self.conn.execute(
            "SELECT id, turn_no, kind, "
            "COALESCE(NULLIF(summary,''), substr(narrative, 1, 160)) AS excerpt FROM turns "
            "WHERE narrative LIKE ? ESCAPE '\\' OR player_input LIKE ? ESCAPE '\\' "
            "OR summary LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
            (pattern, pattern, pattern, int(limit))).fetchall()
        return [dict(r) for r in rows]

    def recall(self, query=None, entity=None, limit=5):
        """查往事：有 entity 先给实体事实时间线；有 query 再做全文检索（trigram FTS → LIKE 兜底）。"""
        out = {"entity": None, "turns": []}
        if entity:
            out["entity"] = self.entity_profile(entity)
        if query:
            query = str(query).strip()
            hits = []
            if self.fts_available and len(query) >= 3:
                try:
                    hits = [dict(r) for r in self.conn.execute(
                        "SELECT t.id, t.turn_no, t.kind, "
                        "COALESCE(NULLIF(t.summary,''), substr(t.narrative,1,160)) AS excerpt "
                        "FROM turns_fts f JOIN turns t ON t.id=f.rowid "
                        "WHERE turns_fts MATCH ? ORDER BY rank LIMIT ?", (_fts_phrase(query), int(limit))).fetchall()]
                except sqlite3.Error:
                    latest = self.conn.execute("SELECT MAX(turn_no) FROM turns").fetchone()[0] or 0
                    log_exception("全文检索失败，使用 LIKE 检索", latest)
                    hits = []
            if not hits:
                hits = self._like_search(query, limit)
            out["turns"] = hits
        return out

    def record_snapshot(self, turn_id, state):
        """给已有回合补一份完整快照（迁移/手动存档用）。"""
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO snapshots(turn_id, state_json) VALUES(?,?)",
                              (int(turn_id), _dump(state)))

    # ---- 读取 ----
    def list_turns(self, limit=50):
        rows = self.conn.execute(
            "SELECT id, turn_no, kind, player_input, narrative, summary, location, game_time, "
            "calendar_days, created_at FROM turns ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def latest_snapshot(self):
        row = self.conn.execute(
            "SELECT turn_id, state_json FROM snapshots ORDER BY turn_id DESC LIMIT 1").fetchone()
        if not row:
            return None, None
        return row["turn_id"], json.loads(row["state_json"])

    def load_state(self, save_key=""):
        """最近快照 + 精确差异，逐字段恢复已提交状态，包括代码结算和删除。"""
        snap_id, data = self.latest_snapshot()
        if data is None:
            return None
        for row in self.conn.execute("SELECT update_json, diff_json FROM state_changes WHERE turn_id>? ORDER BY turn_id", (snap_id,)):
            if row["diff_json"] is not None:
                data = _apply_diff(data, json.loads(row["diff_json"]))
            else:
                # 兼容只有模型增量的早期迁移记录；新状态记录始终写精确差异。
                import world_state
                data = world_state.apply_update(world_state.WorldState(data, save_key=save_key), json.loads(row["update_json"])).data
        return data

    def list_rolls(self, limit=20):
        rows = self.conn.execute(
            "SELECT id, turn_id, kind, payload_json, created_at FROM rolls "
            "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def stats(self):
        def count(table):
            return self.conn.execute("SELECT COUNT(*) AS n FROM " + table).fetchone()["n"]
        return {"turns": count("turns"), "snapshots": count("snapshots"), "rolls": count("rolls")}


    # ---- 每轮自动召回（路线图 §2.5 第一/二层） ----
    def _turn_importance(self, turn_id):
        """代码给回合打重要度（1~10）：掷骰 +4、关系事件 +3、物品/角色/受伤 +2。"""
        row = self.conn.execute(
            "SELECT update_json FROM state_changes WHERE turn_id=?", (turn_id,)).fetchone()
        try:
            update = json.loads(row["update_json"]) if row and row["update_json"] else {}
        except json.JSONDecodeError:
            update = {}
        if not isinstance(update, dict):
            update = {}
        score = 1.0
        if self.conn.execute("SELECT 1 FROM rolls WHERE turn_id=?", (turn_id,)).fetchone():
            score += 4
        if update.get("relationship_events"):
            score += 3
        if update.get("inventory_add") or update.get("inventory_remove"):
            score += 2
        attrs = update.get("player_attributes") or {}
        if isinstance(attrs, dict) and isinstance(attrs.get("hp"), (int, float)):
            score += 2
        if update.get("characters"):
            score += 1
        return min(score, 10.0)

    def recall_for_turn(self, player_input="", state=None, current_turn=0, limit=5, exclude_recent=3):
        """按关键词和实体相关度召回；新近度仅在相关度接近时参与排序。"""
        state = state if isinstance(state, dict) else {}
        text_in = str(player_input or "")
        latest_allowed = int(current_turn or 0) - max(0, int(exclude_recent))
        if latest_allowed < 1 or limit <= 0:
            return []
        entities = [dict(r) for r in self.conn.execute("SELECT id, kind, name FROM entities")]
        aliases = {}
        for row in self.conn.execute("SELECT alias, entity_id FROM aliases"):
            aliases.setdefault(row["entity_id"], []).append(row["alias"])
        names = [e["name"] for e in entities] + [a for group in aliases.values() for a in group]
        total = self.conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        terms, matches = [], {}
        for term in _query_terms(text_in, names):
            hits = self.recall(query=term, limit=max(1, total))["turns"]
            if not hits or len(hits) > total * QUERY_COMMON_RATIO:
                continue
            terms.append(term)
            matches[term] = hits
            if len(terms) >= QUERY_MAX_TERMS:
                break
        candidates = {}

        def add(tid, turn_no, text, entity_weight=0, term=None):
            if turn_no > latest_allowed or turn_no < 1 or not str(text or "").strip():
                return
            c = candidates.setdefault(tid, {"turn_no": turn_no, "text": str(text).strip(),
                                            "entity": 0, "terms": set()})
            c["entity"] = max(c["entity"], entity_weight)
            if term:
                c["terms"].add(term)
                # 摘要漏了查询词时，保留实际命中的正文附近，而非截掉证据。
                if term not in c["text"]:
                    row = self.conn.execute("SELECT narrative, summary, player_input FROM turns WHERE id=?", (tid,)).fetchone()
                    source = next((row[k] for k in ("summary", "narrative", "player_input") if term in (row[k] or "")), str(text))
                    start = max(0, source.find(term) - RECALL_TEXT_CHARS // 3)
                    c["text"] = source[start:start + RECALL_TEXT_CHARS]

        for term, hits in matches.items():
            for h in hits:
                add(h["id"], h["turn_no"], h["excerpt"], term=term)
        state_names = set((state.get("characters") or {}).keys())
        state_names.add((state.get("location") or {}).get("name"))
        for e in entities:
            name, eid = e["name"], e["id"]
            named = any(n in text_in for n in [name] + aliases.get(eid, []))
            if not named and name not in state_names:
                continue
            weight = RECALL_NAMED_WEIGHT if named else RECALL_CONTEXT_WEIGHT
            query = ("SELECT t.id, t.turn_no, COALESCE(NULLIF(t.summary,''), t.narrative) AS text "
                     "FROM mentions m JOIN turns t ON t.id=m.turn_id "
                     "WHERE m.entity_id=? AND t.turn_no<=? ORDER BY t.id DESC")
            if not named:
                query += " LIMIT 1"
            for row in self.conn.execute(query, (eid, latest_allowed)):
                add(row["id"], row["turn_no"], row["text"], weight)
            for f in self.conn.execute(
                    "SELECT f.*, t.turn_no FROM facts f JOIN turns t ON t.id=f.source_turn_id "
                    "WHERE f.entity_id=? AND f.valid_to_turn IS NOT NULL AND f.valid_to_turn<=? "
                    "ORDER BY f.valid_to_turn DESC", (eid, latest_allowed)):
                if not str(f["value"]).strip():
                    continue
                history = f"第 {f['valid_from_turn']}~{f['valid_to_turn']} 回：{name}的{f['attribute']}是「{f['value']}」"
                # 事实与原文共用回合，不用当前事实挤占历史原文。
                add(f["source_turn_id"], f["turn_no"], history, weight)
        scored = []
        for tid, c in candidates.items():
            relevance = RECALL_MATCH_WEIGHT * len(c["terms"]) / max(1, len(terms)) + c["entity"]
            score = (relevance + RECALL_IMPORTANCE_WEIGHT * self._turn_importance(tid) / 10
                     + RECALL_RECENCY_WEIGHT * RECALL_DECAY ** max(0, current_turn - c["turn_no"]))
            scored.append({"turn_no": c["turn_no"], "text": c["text"][:RECALL_TEXT_CHARS], "score": round(score, 4)})
        scored.sort(key=lambda x: (-x["score"], -x["turn_no"]))
        return scored[:int(limit)]


def append_turn(db_path, **kwargs):
    """写库前核对连续性；缺失原文只标缺口，不能用当前状态捏造历史。"""
    with SaveDB(db_path) as db:
        turn_no = int(kwargs.get("turn_no") or 0)
        last = db.conn.execute("SELECT turn_no FROM turns ORDER BY id DESC LIMIT 1").fetchone()
        previous_turn = last["turn_no"] if last else 0
        if previous_turn != turn_no - 1 and turn_no > 1:
            # 使用本轮开始时的 JSON 快照锚定缺口末尾，然后继续正常记录本轮。
            anchor = kwargs.get("state_before")
            if not isinstance(anchor, dict):
                anchor = db.load_state() or {}
            db.record_turn(turn_no - 1, "gap",
                           f"历史库缺口：上一条记录为第 {previous_turn} 回，现从第 {turn_no} 回继续；缺失原文不可恢复。",
                           state=anchor, update={})
        return db.record_turn(**kwargs)


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

    db_path = os.path.join(base, "game" + suffix + ".db")
    db = SaveDB(db_path)
    try:
        db.set_meta("source", "json")
        db.set_meta("save_key", save_key or "")
        db.set_meta("migrated_at", _now())
        db.conn.commit()
        final_turn = max(0, int((state_data.get("meta") or {}).get("turn") or 0))
        records = []
        pending_user = None
        for msg in rounds:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "user":
                pending_user = content
            elif role == "assistant":
                records.append((pending_user, content))
                pending_user = None
        # 未得到回复的尾部输入不算已结算回合；无原文时只留下当前回合的迁移说明。
        if not records:
            records = [(None, state_data.get("narrative_summary") or "（迁移的存档，没有可用的对话原文）")]
        records = records[-max(1, final_turn):]
        first_turn = final_turn - len(records) + 1
        for offset, (player_input, content) in enumerate(records):
            turn_no = first_turn + offset
            db.record_turn(turn_no, "migrated", content, player_input=player_input, update={})
        # 最后一回合写成完整快照，保证 load_state 与 JSON 一致
        last_id = db.conn.execute("SELECT id FROM turns ORDER BY id DESC LIMIT 1").fetchone()["id"]
        db.record_snapshot(last_id, state_data)
        # 用最终状态补一遍实体/事实，保证迁移后可以直接查「某人现在什么态度」
        with db.conn:
            db._sync_entities(last_id, turn_no, None, state_data)
    finally:
        db.close()
    return db_path, len(records)
