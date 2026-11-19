"""Paths and helpers shared by bridge.py and server.py."""
import os
import sqlite3
import sys
from pathlib import Path


def user_folder() -> Path:
    """Per-user folder for chats.txt and data/, outside the installed code. WHATSAPP_CHATS_HOME overrides it."""
    if os.environ.get("WHATSAPP_CHATS_HOME"):
        return Path(os.environ["WHATSAPP_CHATS_HOME"])
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "MLTeam WhatsApp Chats"


HOME = user_folder()
DATA_DIR = HOME / "data"
MESSAGES_DB = DATA_DIR / "messages.db"
SESSION_DB = DATA_DIR / "session.db"
CHATS_FILE = HOME / "chats.txt"
# Groups, phone-numbered and LID-numbered direct chats; not status updates or channels.
CHAT_SERVERS = ("@g.us", "@s.whatsapp.net", "@lid")

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (jid TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS messages (
    chat_jid TEXT, id TEXT, sender TEXT, sender_name TEXT,
    ts INTEGER, text TEXT, PRIMARY KEY (chat_jid, id));
CREATE INDEX IF NOT EXISTS messages_ts ON messages (chat_jid, ts);
CREATE TABLE IF NOT EXISTS documents (
    chat_jid TEXT, id TEXT, file_name TEXT, text TEXT, error TEXT,
    PRIMARY KEY (chat_jid, id));
"""


def load_allowlist(path: Path | None = None) -> tuple[set[str], set[str]]:
    """Read chats.txt into (chat JIDs, casefolded chat names).

    Each line holds a JID (contains @) or an exact group or contact name; text after # is ignored.
    """
    path = path or CHATS_FILE
    jids, names = set(), set()
    if not path.exists():
        return jids, names
    for line in path.read_text().splitlines():
        entry = line.split("#", 1)[0].strip()
        if "@" in entry:
            jids.add(entry)
        elif entry:
            names.add(entry.casefold())
    return jids, names


def reads_all_chats(path: Path | None = None) -> bool:
    """True when chats.txt is missing or lists no chats: then every chat is read."""
    return load_allowlist(path) == (set(), set())


def is_chat(jid: str) -> bool:
    return jid.endswith(CHAT_SERVERS)


def is_allowed(jid: str, name: str, jids: set[str], names: set[str]) -> bool:
    return jid in jids or (bool(name) and name.casefold() in names)


def open_writable() -> sqlite3.Connection:
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(MESSAGES_DB, check_same_thread=False)
    conn.executescript(SCHEMA)
    MESSAGES_DB.chmod(0o600)
    return conn


def open_readonly() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{MESSAGES_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
