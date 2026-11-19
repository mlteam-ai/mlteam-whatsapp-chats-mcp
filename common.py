"""Paths and helpers shared by bridge.py and server.py."""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
MESSAGES_DB = DATA_DIR / "messages.db"
SESSION_DB = DATA_DIR / "session.db"
GROUPS_FILE = ROOT / "groups.txt"

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (jid TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS messages (
    chat_jid TEXT, id TEXT, sender TEXT, sender_name TEXT,
    ts INTEGER, text TEXT, PRIMARY KEY (chat_jid, id));
CREATE INDEX IF NOT EXISTS messages_ts ON messages (chat_jid, ts);
"""


def load_allowlist(path: Path | None = None) -> tuple[set[str], set[str]]:
    """Read groups.txt into (group JIDs, casefolded group names).

    Each line holds a JID (ends in @g.us) or an exact group name; text after # is ignored.
    """
    path = path or GROUPS_FILE
    jids, names = set(), set()
    if not path.exists():
        return jids, names
    for line in path.read_text().splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry.endswith("@g.us"):
            jids.add(entry)
        elif entry:
            names.add(entry.casefold())
    return jids, names


def is_allowed(jid: str, name: str, jids: set[str], names: set[str]) -> bool:
    return jid in jids or (bool(name) and name.casefold() in names)


def open_writable() -> sqlite3.Connection:
    DATA_DIR.mkdir(mode=0o700, exist_ok=True)
    conn = sqlite3.connect(MESSAGES_DB, check_same_thread=False)
    conn.executescript(SCHEMA)
    MESSAGES_DB.chmod(0o600)
    return conn


def open_readonly() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{MESSAGES_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
