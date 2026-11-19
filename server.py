"""Read-only MCP server over data/messages.db.

Opens the DB read-only and returns only groups listed in groups.txt, so
removing a group from the file hides it at once, even if old rows remain.
"""
from datetime import datetime, timezone

from mcp.server.mcpserver import MCPServer

from common import is_allowed, load_allowlist, open_readonly

mcp = MCPServer("whatsapp-groups")


def _since(iso: str | None) -> int:
    return int(datetime.fromisoformat(iso).timestamp()) if iso else 0


def _rows(sql: str, params: list) -> list[dict]:
    jids, names = load_allowlist()
    with open_readonly() as conn:
        chats = conn.execute("SELECT jid, name FROM chats").fetchall()
        allow = sorted({c["jid"] for c in chats if is_allowed(c["jid"], c["name"], jids, names)} | jids)
        if not allow:
            return []
        marks = ",".join("?" * len(allow))
        rows = conn.execute(sql.format(allowed=marks), [*allow, *params]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if "ts" in d:
            d["time"] = datetime.fromtimestamp(d.pop("ts"), timezone.utc).astimezone().isoformat(timespec="minutes")
        out.append(d)
    return out


@mcp.tool()
def list_groups() -> list[dict]:
    """List the WhatsApp groups you may read, with message counts and date range."""
    return _rows(
        """SELECT m.chat_jid AS jid, c.name, COUNT(*) AS messages,
                  datetime(MIN(m.ts), 'unixepoch') AS first_utc,
                  datetime(MAX(m.ts), 'unixepoch') AS last_utc
           FROM messages m LEFT JOIN chats c ON c.jid = m.chat_jid
           WHERE m.chat_jid IN ({allowed}) GROUP BY m.chat_jid""",
        [],
    )


@mcp.tool()
def search_messages(query: str, group_jid: str | None = None, since: str | None = None, limit: int = 50) -> list[dict]:
    """Search message text (case-insensitive substring) in allowed groups.

    group_jid: limit to one group. since: ISO date like 2026-11-01. Newest first.
    """
    # shortcut: LIKE scan, fine for tens of thousands of messages; switch to FTS5 if slow.
    return _rows(
        """SELECT c.name AS grp, m.sender_name, m.sender, m.ts, m.text
           FROM messages m LEFT JOIN chats c ON c.jid = m.chat_jid
           WHERE m.chat_jid IN ({allowed}) AND m.text LIKE ? AND m.ts >= ?
             AND (? IS NULL OR m.chat_jid = ?)
           ORDER BY m.ts DESC LIMIT ?""",
        [f"%{query}%", _since(since), group_jid, group_jid, min(limit, 500)],
    )


@mcp.tool()
def get_messages(group_jid: str, since: str | None = None, limit: int = 200) -> list[dict]:
    """Read a group's messages in time order. since: ISO date; default returns the latest `limit` messages."""
    rows = _rows(
        """SELECT m.sender_name, m.sender, m.ts, m.text FROM messages m
           WHERE m.chat_jid IN ({allowed}) AND m.chat_jid = ? AND m.ts >= ?
           ORDER BY m.ts DESC LIMIT ?""",
        [group_jid, _since(since), min(limit, 1000)],
    )
    return rows[::-1]


if __name__ == "__main__":
    mcp.run()
