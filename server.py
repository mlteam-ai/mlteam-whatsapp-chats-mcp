"""Read-only MCP server over data/messages.db.

Opens the DB read-only and returns only chats listed in chats.txt, so
removing a chat from the file hides it at once, even if old rows remain.
A missing or empty chats.txt returns every stored chat.
"""
import sqlite3
from datetime import datetime, timezone

from mcp.server.mcpserver import MCPServer

from common import is_allowed, load_allowlist, open_readonly, reads_all_chats

mcp = MCPServer("whatsapp-chats")

# Shown on document messages so the model knows whether get_document will work.
DOC_STATUS = """CASE
    WHEN d.id IS NOT NULL AND d.error != '' THEN 'unreadable: ' || d.error
    WHEN d.id IS NOT NULL THEN 'text available (' || length(d.text) || ' chars): call get_document'
    WHEN m.text LIKE '[document %' THEN 'text not available (not downloaded)'
END AS document"""


def _since(iso: str | None) -> int:
    return int(datetime.fromisoformat(iso).timestamp()) if iso else 0


def _rows(sql: str, params: list) -> list[dict]:
    jids, names = load_allowlist()
    every = reads_all_chats()
    try:
        with open_readonly() as conn:
            chats = conn.execute("SELECT jid, name FROM chats").fetchall()
            allow = sorted({c["jid"] for c in chats if every or is_allowed(c["jid"], c["name"], jids, names)} | jids)
            if not allow:
                return []
            marks = ",".join("?" * len(allow))
            rows = conn.execute(sql.format(allowed=marks), [*allow, *params]).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            raise RuntimeError("Database is from an older version: restart bridge.py once to upgrade it.") from e
        raise
    out = []
    for r in rows:
        d = {k: v for k, v in dict(r).items() if v is not None}
        if "ts" in d:
            d["time"] = datetime.fromtimestamp(d.pop("ts"), timezone.utc).astimezone().isoformat(timespec="minutes")
        out.append(d)
    return out


def _snippet(text: str, query: str, width: int = 300) -> str:
    i = text.casefold().find(query.casefold())
    start = max(0, i - width // 2)
    return ("…" if start else "") + text[start:start + width].replace("\n", " ") + "…"


@mcp.tool()
def list_chats() -> list[dict]:
    """List the WhatsApp chats (groups and direct chats) you may read, with message counts and date range."""
    return _rows(
        """SELECT m.chat_jid AS jid, c.name, COUNT(*) AS messages,
                  datetime(MIN(m.ts), 'unixepoch') AS first_utc,
                  datetime(MAX(m.ts), 'unixepoch') AS last_utc
           FROM messages m LEFT JOIN chats c ON c.jid = m.chat_jid
           WHERE m.chat_jid IN ({allowed}) GROUP BY m.chat_jid""",
        [],
    )


@mcp.tool()
def search_messages(query: str, chat_jid: str | None = None, since: str | None = None, limit: int = 50) -> list[dict]:
    """Search message text and attached document text (case-insensitive substring) in allowed chats.

    chat_jid: limit to one chat. since: ISO date like 2026-11-01. Newest first.
    Document matches include a snippet; read the full text with get_document.
    """
    # shortcut: LIKE scan, fine for tens of thousands of messages; switch to FTS5 if slow.
    rows = _rows(
        f"""SELECT m.chat_jid, c.name AS chat, m.id, m.sender_name, m.sender, m.ts, m.text,
                   {DOC_STATUS}, d.text AS doc_text
            FROM messages m LEFT JOIN chats c ON c.jid = m.chat_jid
            LEFT JOIN documents d ON d.chat_jid = m.chat_jid AND d.id = m.id
            WHERE m.chat_jid IN ({{allowed}}) AND (m.text LIKE ? OR d.text LIKE ?) AND m.ts >= ?
              AND (? IS NULL OR m.chat_jid = ?)
            ORDER BY m.ts DESC LIMIT ?""",
        [f"%{query}%"] * 2 + [_since(since), chat_jid, chat_jid, min(limit, 500)],
    )
    for r in rows:
        doc_text = r.pop("doc_text", "")
        if query.casefold() in doc_text.casefold():
            r["document_snippet"] = _snippet(doc_text, query)
    return rows


@mcp.tool()
def get_messages(chat_jid: str, since: str | None = None, limit: int = 200) -> list[dict]:
    """Read a chat's messages in time order. since: ISO date; default returns the latest `limit` messages."""
    rows = _rows(
        f"""SELECT m.id, m.sender_name, m.sender, m.ts, m.text, {DOC_STATUS}
            FROM messages m LEFT JOIN documents d ON d.chat_jid = m.chat_jid AND d.id = m.id
            WHERE m.chat_jid IN ({{allowed}}) AND m.chat_jid = ? AND m.ts >= ?
            ORDER BY m.ts DESC LIMIT ?""",
        [chat_jid, _since(since), min(limit, 1000)],
    )
    return rows[::-1]


@mcp.tool()
def get_document(chat_jid: str, message_id: str, offset: int = 0, max_chars: int = 20000) -> dict:
    """Read the extracted text of a document attached to a message, in pages.

    Use the chat_jid and id from search_messages or get_messages. For long documents,
    call again with offset = next_offset until it is absent.
    """
    rows = _rows(
        """SELECT file_name, text, error FROM documents
           WHERE chat_jid IN ({allowed}) AND chat_jid = ? AND id = ?""",
        [chat_jid, message_id],
    )
    if not rows:
        return {"error": "no extracted document for this message in an allowed chat"}
    doc = rows[0]
    if doc.get("error"):
        return {"file_name": doc["file_name"], "error": doc["error"]}
    text, max_chars = doc["text"], min(max(max_chars, 1000), 100_000)
    out = {"file_name": doc["file_name"], "total_chars": len(text), "text": text[offset:offset + max_chars]}
    if offset + max_chars < len(text):
        out["next_offset"] = offset + max_chars
    return out


if __name__ == "__main__":
    mcp.run()
