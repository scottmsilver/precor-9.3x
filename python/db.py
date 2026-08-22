"""TreadmillDB — SQLite persistence layer replacing flat JSON files.

Provides multi-user profile support with per-profile isolation of runs,
workouts, program history, and coach messages.  Two connections are used:
*read* (main/event-loop thread) and *write* (via asyncio.to_thread for
blocking I/O).  WAL mode is enabled for concurrent reads during writes.

For :memory: databases, shared-cache URIs are used so both connections
see the same data.
"""

import json
import logging
import os
import sqlite3
import time
import uuid

log = logging.getLogger("treadmill.db")

GUEST_PROFILE_ID = "00000000-0000-0000-0000-000000000000"
DEFAULT_WEIGHT_LBS = 154

MAX_HISTORY = 20
MAX_CHAT = 40

# Columns to SELECT for profiles — never returns the raw avatar BLOB,
# instead returns ``has_avatar`` (0/1).
_PROFILE_COLS = (
    "id, name, color, initials, avatar IS NOT NULL as has_avatar, " "weight_lbs, vest_lbs, created_at, updated_at"
)


class TreadmillDB:
    """SQLite persistence layer for treadmill multi-user profiles."""

    def __init__(self, db_path=":memory:"):
        self._db_path = db_path
        if db_path == ":memory:":
            uri = f"file:treadmill_{id(self)}?mode=memory&cache=shared"
            self._read = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self._write = sqlite3.connect(uri, uri=True, check_same_thread=False)
        else:
            self._read = sqlite3.connect(db_path, check_same_thread=False)
            self._write = sqlite3.connect(db_path, check_same_thread=False)
        self._read.row_factory = sqlite3.Row
        self._write.row_factory = sqlite3.Row
        for conn in (self._read, self._write):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_schema(self):
        w = self._write
        w.executescript(
            """
            CREATE TABLE IF NOT EXISTS migration_version (
                version INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                color TEXT,
                initials TEXT,
                avatar BLOB,
                weight_lbs INTEGER DEFAULT 154,
                vest_lbs INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                started_at TEXT,
                ended_at TEXT,
                elapsed REAL,
                distance REAL,
                vert_feet REAL,
                calories REAL,
                end_reason TEXT,
                program_name TEXT,
                program_fingerprint TEXT,
                program_completed INTEGER DEFAULT 0,
                is_manual INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS saved_workouts (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                program_json TEXT NOT NULL,
                source TEXT,
                prompt TEXT,
                times_used INTEGER DEFAULT 0,
                last_used_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS program_history (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                program_json TEXT NOT NULL,
                source TEXT,
                prompt TEXT,
                total_duration INTEGER,
                completed INTEGER DEFAULT 0,
                last_interval INTEGER DEFAULT 0,
                last_elapsed INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS coach_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                message_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """
        )
        # Ensure guest profile exists
        now = _now()
        w.execute(
            "INSERT OR IGNORE INTO profiles (id, name, color, initials, weight_lbs, vest_lbs, created_at, updated_at) "
            "VALUES (?, 'Guest', '#888888', 'G', ?, 0, ?, ?)",
            (GUEST_PROFILE_ID, DEFAULT_WEIGHT_LBS, now, now),
        )
        w.commit()

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    def get_profiles(self, include_guest=False):
        """Return all profiles as list of dicts.  Excludes guest by default."""
        if include_guest:
            rows = self._read.execute(f"SELECT {_PROFILE_COLS} FROM profiles ORDER BY created_at").fetchall()
        else:
            rows = self._read.execute(
                f"SELECT {_PROFILE_COLS} FROM profiles WHERE id != ? ORDER BY created_at",
                (GUEST_PROFILE_ID,),
            ).fetchall()
        return [self._profile_to_dict(r) for r in rows]

    @staticmethod
    def _profile_to_dict(row):
        """Convert a profile row to dict with has_avatar as bool."""
        if not row:
            return None
        d = dict(row)
        d["has_avatar"] = bool(d.get("has_avatar"))
        return d

    def profile_count(self):
        """Number of non-guest profiles."""
        row = self._read.execute("SELECT COUNT(*) as cnt FROM profiles WHERE id != ?", (GUEST_PROFILE_ID,)).fetchone()
        return row["cnt"]

    def get_profile(self, profile_id):
        """Return single profile dict or None."""
        row = self._read.execute(f"SELECT {_PROFILE_COLS} FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        return self._profile_to_dict(row)

    def create_profile(self, name, color="#4A90D9", initials=None, weight_lbs=DEFAULT_WEIGHT_LBS, vest_lbs=0):
        pid = str(uuid.uuid4())
        now = _now()
        if initials is None:
            initials = _make_initials(name)
        self._write.execute(
            "INSERT INTO profiles (id, name, color, initials, weight_lbs, vest_lbs, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, name, color, initials, weight_lbs, vest_lbs, now, now),
        )
        self._write.commit()
        return self.get_profile(pid)

    def update_profile(self, profile_id, **kwargs):
        """Update profile fields.  Supported: name, color, initials, weight_lbs, vest_lbs."""
        allowed = {"name", "color", "initials", "weight_lbs", "vest_lbs"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return self.get_profile(profile_id)
        parts = [f"{k} = ?" for k in updates]
        parts.append("updated_at = ?")
        vals = list(updates.values()) + [_now(), profile_id]
        self._write.execute(f"UPDATE profiles SET {', '.join(parts)} WHERE id = ?", vals)
        self._write.commit()
        return self.get_profile(profile_id)

    def delete_profile(self, profile_id):
        """Delete profile and all associated data (CASCADE)."""
        if profile_id == GUEST_PROFILE_ID:
            return False
        cur = self._write.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        self._write.commit()
        return cur.rowcount > 0

    # --- Avatar ---

    def get_avatar(self, profile_id):
        row = self._read.execute("SELECT avatar FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        return row["avatar"] if row else None

    def set_avatar(self, profile_id, data: bytes):
        self._write.execute("UPDATE profiles SET avatar = ?, updated_at = ? WHERE id = ?", (data, _now(), profile_id))
        self._write.commit()

    def clear_avatar(self, profile_id):
        self._write.execute("UPDATE profiles SET avatar = NULL, updated_at = ? WHERE id = ?", (_now(), profile_id))
        self._write.commit()

    # ------------------------------------------------------------------
    # Active profile (app_state)
    # ------------------------------------------------------------------

    def get_active_profile_id(self):
        row = self._read.execute("SELECT value FROM app_state WHERE key = 'active_profile'").fetchone()
        return row["value"] if row else None

    def set_active_profile_id(self, profile_id):
        self._write.execute(
            "INSERT OR REPLACE INTO app_state (key, value) VALUES ('active_profile', ?)",
            (profile_id,),
        )
        self._write.commit()

    def clear_active_profile(self):
        self._write.execute("DELETE FROM app_state WHERE key = 'active_profile'")
        self._write.commit()

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def insert_run(self, profile_id, run_dict):
        """Insert a run record.  Returns the run id."""
        return self._insert_run_row(profile_id, run_dict)

    def update_run(self, run_id, **kwargs):
        allowed = {"ended_at", "elapsed", "distance", "vert_feet", "calories", "end_reason", "program_completed"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        parts = [f"{k} = ?" for k in updates]
        parts.append("updated_at = ?")
        vals = list(updates.values()) + [_now(), run_id]
        self._write.execute(f"UPDATE runs SET {', '.join(parts)} WHERE id = ?", vals)
        self._write.commit()

    def get_runs(self, profile_id, limit=200):
        rows = self._read.execute(
            "SELECT * FROM runs WHERE profile_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (profile_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id):
        row = self._read.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def _insert_run_row(self, profile_id, d):
        rid = d.get("id") or str(uuid.uuid4())
        now = _now()
        self._write.execute(
            "INSERT INTO runs (id, profile_id, started_at, ended_at, elapsed, distance, vert_feet, "
            "calories, end_reason, program_name, program_fingerprint, program_completed, is_manual, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                profile_id,
                d.get("started_at"),
                d.get("ended_at"),
                d.get("elapsed", 0),
                d.get("distance", 0),
                d.get("vert_feet", 0),
                d.get("calories", 0),
                d.get("end_reason", "in_progress"),
                d.get("program_name"),
                d.get("program_fingerprint"),
                1 if d.get("program_completed") else 0,
                1 if d.get("is_manual") else 0,
                now,
                now,
            ),
        )
        self._write.commit()
        return rid

    # ------------------------------------------------------------------
    # Saved workouts
    # ------------------------------------------------------------------

    def save_workout(self, profile_id, program, source="generated", prompt=""):
        wid = str(uuid.uuid4())
        now = _now()
        self._write.execute(
            "INSERT INTO saved_workouts (id, profile_id, name, program_json, source, prompt, "
            "times_used, last_used_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                wid,
                profile_id,
                program.get("name", "Untitled"),
                json.dumps(program),
                source,
                prompt,
                0,
                None,
                now,
                now,
            ),
        )
        self._write.commit()
        return self._workout_row_to_dict(
            self._read.execute("SELECT * FROM saved_workouts WHERE id = ?", (wid,)).fetchone()
        )

    def get_saved_workouts(self, profile_id):
        rows = self._read.execute(
            "SELECT * FROM saved_workouts WHERE profile_id = ? ORDER BY last_used_at DESC, created_at DESC",
            (profile_id,),
        ).fetchall()
        return [self._workout_row_to_dict(r) for r in rows]

    def get_saved_workout(self, workout_id):
        row = self._read.execute("SELECT * FROM saved_workouts WHERE id = ?", (workout_id,)).fetchone()
        return self._workout_row_to_dict(row) if row else None

    def rename_workout(self, workout_id, name):
        program_row = self._read.execute(
            "SELECT program_json FROM saved_workouts WHERE id = ?", (workout_id,)
        ).fetchone()
        if not program_row:
            return None
        program = json.loads(program_row["program_json"])
        program["name"] = name
        self._write.execute(
            "UPDATE saved_workouts SET name = ?, program_json = ?, updated_at = ? WHERE id = ?",
            (name, json.dumps(program), _now(), workout_id),
        )
        self._write.commit()
        return self._workout_row_to_dict(
            self._read.execute("SELECT * FROM saved_workouts WHERE id = ?", (workout_id,)).fetchone()
        )

    def delete_workout(self, workout_id):
        cur = self._write.execute("DELETE FROM saved_workouts WHERE id = ?", (workout_id,))
        self._write.commit()
        return cur.rowcount > 0

    def update_workout_usage(self, workout_id):
        now = _now()
        self._write.execute(
            "UPDATE saved_workouts SET times_used = times_used + 1, last_used_at = ?, updated_at = ? WHERE id = ?",
            (now, now, workout_id),
        )
        self._write.commit()

    def _workout_row_to_dict(self, row):
        if not row:
            return None
        d = dict(row)
        d["program"] = json.loads(d.pop("program_json"))
        d["total_duration"] = sum(iv.get("duration", 0) for iv in d["program"].get("intervals", []))
        return d

    # ------------------------------------------------------------------
    # Program history
    # ------------------------------------------------------------------

    def add_to_history(self, profile_id, program, prompt="", source=None):
        """Add program to history.  Deduplicates by name within profile.  Returns entry dict."""
        name = program.get("name", "Untitled")
        # Remove existing entry with same name for this profile
        self._write.execute(
            "DELETE FROM program_history WHERE profile_id = ? AND name = ?",
            (profile_id, name),
        )
        hid = str(uuid.uuid4())
        now = _now()
        total_dur = sum(iv.get("duration", 0) for iv in program.get("intervals", []))
        self._write.execute(
            "INSERT INTO program_history (id, profile_id, name, program_json, source, prompt, "
            "total_duration, completed, last_interval, last_elapsed, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (hid, profile_id, name, json.dumps(program), source, prompt, total_dur, 0, 0, 0, now),
        )
        # Enforce cap: keep newest MAX_HISTORY entries per profile
        self._write.execute(
            "DELETE FROM program_history WHERE profile_id = ? AND id NOT IN "
            "(SELECT id FROM program_history WHERE profile_id = ? ORDER BY created_at DESC LIMIT ?)",
            (profile_id, profile_id, MAX_HISTORY),
        )
        self._write.commit()
        return self._history_row_to_dict(
            self._read.execute("SELECT * FROM program_history WHERE id = ?", (hid,)).fetchone()
        )

    def get_program_history(self, profile_id):
        rows = self._read.execute(
            "SELECT * FROM program_history WHERE profile_id = ? ORDER BY created_at DESC",
            (profile_id,),
        ).fetchall()
        return [self._history_row_to_dict(r) for r in rows]

    def get_history_entry(self, entry_id):
        row = self._read.execute("SELECT * FROM program_history WHERE id = ?", (entry_id,)).fetchone()
        return self._history_row_to_dict(row) if row else None

    def update_history_entry(self, entry_id, completed=None, last_interval=None, last_elapsed=None, program=None):
        parts = []
        vals = []
        if program is not None:
            # The plan can be reshaped while it runs (skip truncates, extend/split
            # resize), and last_interval/last_elapsed are positions *in that plan* —
            # so they have to be stored together or resume measures against a
            # timeline that no longer exists.
            parts.append("program_json = ?")
            vals.append(json.dumps(program))
            parts.append("total_duration = ?")
            vals.append(sum(iv.get("duration", 0) for iv in program.get("intervals", [])))
        if completed is not None:
            parts.append("completed = ?")
            vals.append(1 if completed else 0)
        if last_interval is not None:
            parts.append("last_interval = ?")
            vals.append(last_interval)
        if last_elapsed is not None:
            parts.append("last_elapsed = ?")
            vals.append(last_elapsed)
        if not parts:
            return
        vals.append(entry_id)
        self._write.execute(f"UPDATE program_history SET {', '.join(parts)} WHERE id = ?", vals)
        self._write.commit()

    def _history_row_to_dict(self, row):
        if not row:
            return None
        d = dict(row)
        d["program"] = json.loads(d.pop("program_json"))
        d["completed"] = bool(d.get("completed"))
        return d

    # ------------------------------------------------------------------
    # Coach messages
    # ------------------------------------------------------------------

    def add_chat_message(self, profile_id, message):
        """Append a chat message (dict) to the profile's coach history."""
        now = _now()
        self._write.execute(
            "INSERT INTO coach_messages (profile_id, message_json, created_at) VALUES (?,?,?)",
            (profile_id, json.dumps(message), now),
        )
        # Enforce cap
        self._write.execute(
            "DELETE FROM coach_messages WHERE profile_id = ? AND id NOT IN "
            "(SELECT id FROM coach_messages WHERE profile_id = ? ORDER BY id DESC LIMIT ?)",
            (profile_id, profile_id, MAX_CHAT),
        )
        self._write.commit()

    def get_chat_history(self, profile_id, limit=20):
        """Return last N chat messages in chronological order."""
        rows = self._read.execute(
            "SELECT * FROM ("
            "  SELECT id, message_json, created_at FROM coach_messages "
            "  WHERE profile_id = ? ORDER BY id DESC LIMIT ?"
            ") sub ORDER BY id ASC",
            (profile_id, limit),
        ).fetchall()
        return [json.loads(r["message_json"]) for r in rows]

    # ------------------------------------------------------------------
    # Migration from JSON files
    # ------------------------------------------------------------------

    def migrate_from_json(self, profile_id, *, history_file=None, workouts_file=None, runs_file=None, user_file=None):
        """Migrate data from flat JSON files into the given profile.

        Files are renamed with .migrated suffix AFTER successful commit.
        Idempotent: if migration_version >= 1, skips.
        """
        row = self._read.execute("SELECT version FROM migration_version WHERE version = 1").fetchone()
        if row:
            log.info("Migration already done (version 1), skipping")
            return

        files_to_rename = []
        self._write.execute("BEGIN")
        try:
            # User profile data
            if user_file and os.path.isfile(user_file):
                try:
                    with open(user_file) as f:
                        user = json.load(f)
                    self._write.execute(
                        "UPDATE profiles SET weight_lbs = ?, vest_lbs = ?, updated_at = ? WHERE id = ?",
                        (user.get("weight_lbs", DEFAULT_WEIGHT_LBS), user.get("vest_lbs", 0), _now(), profile_id),
                    )
                    files_to_rename.append(user_file)
                except (json.JSONDecodeError, OSError) as e:
                    log.warning("Skipping corrupted user file %s: %s", user_file, e)

            # Run history
            if runs_file and os.path.isfile(runs_file):
                try:
                    with open(runs_file) as f:
                        runs = json.load(f)
                    for r in runs:
                        try:
                            self._write.execute(
                                "INSERT OR IGNORE INTO runs (id, profile_id, started_at, ended_at, elapsed, distance, "
                                "vert_feet, calories, end_reason, program_name, program_fingerprint, "
                                "program_completed, is_manual, created_at, updated_at) "
                                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (
                                    r.get("id", str(uuid.uuid4())),
                                    profile_id,
                                    r.get("started_at"),
                                    r.get("ended_at"),
                                    r.get("elapsed", 0),
                                    r.get("distance", 0),
                                    r.get("vert_feet", 0),
                                    r.get("calories", 0),
                                    r.get("end_reason"),
                                    r.get("program_name"),
                                    r.get("program_fingerprint"),
                                    1 if r.get("program_completed") else 0,
                                    1 if r.get("is_manual") else 0,
                                    r.get("started_at") or _now(),
                                    _now(),
                                ),
                            )
                        except Exception as e:
                            log.warning("Skipping malformed run entry: %s", e)
                    files_to_rename.append(runs_file)
                except (json.JSONDecodeError, OSError) as e:
                    log.warning("Skipping corrupted runs file %s: %s", runs_file, e)

            # Program history
            if history_file and os.path.isfile(history_file):
                try:
                    with open(history_file) as f:
                        history = json.load(f)
                    for h in history:
                        try:
                            program = h.get("program", {})
                            self._write.execute(
                                "INSERT OR IGNORE INTO program_history "
                                "(id, profile_id, name, program_json, source, prompt, "
                                "total_duration, completed, last_interval, last_elapsed, created_at) "
                                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                (
                                    h.get("id", str(uuid.uuid4())),
                                    profile_id,
                                    program.get("name", "Untitled"),
                                    json.dumps(program),
                                    None,
                                    h.get("prompt", ""),
                                    h.get("total_duration", 0),
                                    1 if h.get("completed") else 0,
                                    h.get("last_interval", 0),
                                    h.get("last_elapsed", 0),
                                    h.get("created_at") or _now(),
                                ),
                            )
                        except Exception as e:
                            log.warning("Skipping malformed history entry: %s", e)
                    files_to_rename.append(history_file)
                except (json.JSONDecodeError, OSError) as e:
                    log.warning("Skipping corrupted history file %s: %s", history_file, e)

            # Saved workouts
            if workouts_file and os.path.isfile(workouts_file):
                try:
                    with open(workouts_file) as f:
                        workouts = json.load(f)
                    for w in workouts:
                        try:
                            program = w.get("program", {})
                            self._write.execute(
                                "INSERT OR IGNORE INTO saved_workouts "
                                "(id, profile_id, name, program_json, source, prompt, "
                                "times_used, last_used_at, created_at, updated_at) "
                                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (
                                    w.get("id", str(uuid.uuid4())),
                                    profile_id,
                                    w.get("name", program.get("name", "Untitled")),
                                    json.dumps(program),
                                    w.get("source"),
                                    w.get("prompt", ""),
                                    w.get("times_used", 0),
                                    w.get("last_used"),
                                    w.get("created_at") or _now(),
                                    _now(),
                                ),
                            )
                        except Exception as e:
                            log.warning("Skipping malformed workout entry: %s", e)
                    files_to_rename.append(workouts_file)
                except (json.JSONDecodeError, OSError) as e:
                    log.warning("Skipping corrupted workouts file %s: %s", workouts_file, e)

            # Mark migration complete
            self._write.execute("INSERT OR IGNORE INTO migration_version (version) VALUES (1)")
            self._write.execute("COMMIT")
        except Exception:
            self._write.execute("ROLLBACK")
            raise

        # Rename files AFTER successful commit
        for fp in files_to_rename:
            try:
                os.rename(fp, fp + ".migrated")
            except OSError as e:
                log.warning("Could not rename %s: %s", fp, e)

    # ------------------------------------------------------------------
    # Guest conversion
    # ------------------------------------------------------------------

    def convert_guest(self, new_profile_id):
        """Transfer ALL guest data (runs, history, workouts, chat) to a profile.

        Performed in a single transaction.
        """
        self._write.execute("BEGIN")
        try:
            self._write.execute(
                "UPDATE runs SET profile_id = ? WHERE profile_id = ?",
                (new_profile_id, GUEST_PROFILE_ID),
            )
            self._write.execute(
                "UPDATE program_history SET profile_id = ? WHERE profile_id = ?",
                (new_profile_id, GUEST_PROFILE_ID),
            )
            self._write.execute(
                "UPDATE saved_workouts SET profile_id = ? WHERE profile_id = ?",
                (new_profile_id, GUEST_PROFILE_ID),
            )
            self._write.execute(
                "UPDATE coach_messages SET profile_id = ? WHERE profile_id = ?",
                (new_profile_id, GUEST_PROFILE_ID),
            )
            self._write.execute("COMMIT")
        except Exception:
            self._write.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self):
        self._read.close()
        self._write.close()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _make_initials(name):
    parts = name.strip().split()
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()
