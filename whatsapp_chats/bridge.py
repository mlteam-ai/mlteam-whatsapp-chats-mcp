"""Read-only WhatsApp bridge.

Links to your account as a device and stores the text of messages, and of
attached documents (PDF, DOCX, XLSX, PPTX, plain text), from the chats (groups
and direct chats) listed in chats.txt into data/messages.db. If chats.txt is
missing or empty, every chat is stored. Document files are not kept, only their
text. It never sends messages and opens no network ports.

    whatsapp-chats           # link (first run) and store messages; keep it running
    whatsapp-chats --list    # after linking: print your groups and contacts with their JIDs
    whatsapp-chats folder    # print the folder holding chats.txt and your data
    whatsapp-chats server    # the MCP server; Claude starts it, you don't

WhatsApp sends past history only once, right after linking, so linking happens
in the normal run where that history gets stored.
"""
import argparse
import queue
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

from neonize.client import NewClient
from neonize.events import ConnectedEv, HistorySyncEv, MessageEv
from neonize.proto.waCompanionReg.WAWebProtobufsCompanionReg_pb2 import DeviceProps
from neonize.utils.jid import Jid2String

from .common import (CHATS_FILE, DATA_DIR, HOME, SESSION_DB, is_allowed, is_chat, load_allowlist, open_writable,
                    reads_all_chats)
from .extract import SUPPORTED

# Asked for at link time; the phone may send less.
HISTORY_DAYS = 365

EXTRACTOR = Path(__file__).resolve().parent / "extract.py"
MAX_DOC_BYTES = 25 * 1024 * 1024
MAX_DOC_CHARS = 2_000_000
EXTRACT_TIMEOUT = 120  # seconds

DEVICE = DeviceProps(
    os="MLTeam WhatsApp Chats Reader (read-only)",  # shown in WhatsApp > Linked devices
    platformType=DeviceProps.SAFARI,
    requireFullSync=True,
    historySyncConfig=DeviceProps.HistorySyncConfig(fullSyncDaysLimit=HISTORY_DAYS),
)

db = None
db_lock = threading.Lock()
allow: set[str] = set()  # JIDs to store; chat names from chats.txt resolve into it
names: set[str] = set()
every_chat = False  # chats.txt is missing or empty: store all chats
documents: queue.Queue = queue.Queue()  # (chat, msg_id, message) waiting for text extraction


def unwrap(m):
    """Strip wrappers that history sync leaves on (ephemeral, view-once, captioned documents)."""
    for _ in range(3):
        for field in ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2", "documentWithCaptionMessage"):
            if m.HasField(field):
                m = getattr(m, field).message
                break
        else:
            return m
    return m


def message_text(m) -> str:
    """Readable text for a waE2E.Message; empty string for unsupported types."""
    if m.conversation:
        return m.conversation
    if m.HasField("extendedTextMessage"):
        return m.extendedTextMessage.text
    if m.HasField("imageMessage"):
        return "[image] " + m.imageMessage.caption
    if m.HasField("videoMessage"):
        return "[video] " + m.videoMessage.caption
    if m.HasField("documentMessage"):
        d = m.documentMessage
        return f"[document {d.fileName}] {d.caption}"
    if m.HasField("audioMessage"):
        return "[audio]"
    for field in ("pollCreationMessage", "pollCreationMessageV3"):
        if m.HasField(field):
            p = getattr(m, field)
            return "[poll] " + " | ".join([p.name, *(o.optionName for o in p.options)])
    return ""


def is_linked(session_db: Path) -> bool:
    """True if the session DB holds a paired device (whatsmeow_device row)."""
    if not session_db.exists():
        return False
    try:
        with sqlite3.connect(f"file:{session_db}?mode=ro", uri=True) as conn:
            return conn.execute("SELECT COUNT(*) FROM whatsmeow_device").fetchone()[0] > 0
    except sqlite3.OperationalError:  # no table yet: pairing never finished
        return False


def contact_name(info) -> str:
    return info.FullName or info.FirstName or info.PushName or info.BusinessName


def joined_chats(client: NewClient) -> list[tuple[str, str]]:
    """(JID, name) of your groups and contacts. Contacts can be missing right after linking."""
    chats = [(Jid2String(g.JID), g.GroupName.Name) for g in client.get_joined_groups()]
    return chats + [(Jid2String(c.JID), contact_name(c.Info)) for c in client.contact.get_all_contacts()]


def wanted(jid: str, name: str) -> bool:
    if every_chat:
        return is_chat(jid)
    return is_allowed(jid, name, allow, names)


def phone_jid(jid: str, alt: str) -> str:
    """Direct chats can arrive under a LID (hidden number); file them under the phone JID when known."""
    return alt if jid.endswith("@lid") and alt.endswith("@s.whatsapp.net") else jid


def remember_chat(jid: str, name: str) -> None:
    allow.add(jid)
    with db_lock:
        db.execute("INSERT OR REPLACE INTO chats VALUES (?,?)", (jid, name))
        db.commit()


def save(chat: str, msg_id: str, sender: str, name: str, ts: int, message) -> None:
    if chat not in allow:
        # A chat first seen while running: in a direct chat the other person's name names the chat.
        chat_name = "" if chat.endswith("@g.us") or name == "me" else name
        if not wanted(chat, chat_name):
            return
        remember_chat(chat, chat_name)
    message = unwrap(message)
    text = message_text(message).strip()
    if not text:
        return
    # shortcut: guesses ms vs s by size; fine until year 5138.
    if ts > 100_000_000_000:
        ts //= 1000
    with db_lock:
        db.execute(
            "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?)",
            (chat, msg_id, sender, name, ts, text),
        )
        db.commit()
    if message.HasField("documentMessage"):
        documents.put((chat, msg_id, message))


def extract_document(client: NewClient, message) -> str:
    """Download a document and return its text. Raises ValueError with a short reason."""
    d = message.documentMessage
    ext = "." + d.fileName.rsplit(".", 1)[-1].lower() if "." in d.fileName else ""
    if ext not in SUPPORTED:
        raise ValueError(f"unsupported file type {ext or '(none)'}")
    if d.fileLength > MAX_DOC_BYTES:
        raise ValueError(f"larger than {MAX_DOC_BYTES // 1024 // 1024} MB")
    try:
        data = client.download_any(message)
    except Exception as e:
        raise ValueError(f"download failed, media may have expired: {e}") from e
    if not data:
        raise ValueError("download failed, media may have expired")
    try:
        r = subprocess.run([sys.executable, "-I", str(EXTRACTOR), d.fileName], input=data,
                           capture_output=True, timeout=EXTRACT_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise ValueError(f"text extraction took over {EXTRACT_TIMEOUT}s") from e
    if r.returncode:
        lines = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ValueError("could not read file: " + (lines[-1] if lines else f"exit {r.returncode}"))
    return r.stdout.decode("utf-8", "replace")[:MAX_DOC_CHARS]


def process_document(client: NewClient, chat: str, msg_id: str, message) -> None:
    with db_lock:
        if db.execute("SELECT 1 FROM documents WHERE chat_jid=? AND id=?", (chat, msg_id)).fetchone():
            return
    text, error = "", ""
    try:
        text = extract_document(client, message)
    except ValueError as e:
        error = str(e)[:300]
    with db_lock:
        db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?)",
                   (chat, msg_id, message.documentMessage.fileName, text, error))
        db.commit()


def document_worker(client: NewClient) -> None:
    """Extracts queued documents one at a time, off the WhatsApp event thread."""
    while True:
        process_document(client, *documents.get())


def on_connected(client: NewClient, _: ConnectedEv) -> None:
    matched: dict[str, list[str]] = {}
    for jid, name in joined_chats(client):
        if wanted(jid, name):
            remember_chat(jid, name)
            if name:
                matched.setdefault(name.casefold(), []).append(jid)
    for n in sorted(names):
        hits = matched.get(n, [])
        if not hits:
            print(f"warning: no chat named {n!r} (names must match exactly)", file=sys.stderr)
        elif len(hits) > 1:
            print(f"warning: {len(hits)} chats named {n!r}; reading all. Use JIDs to pick one.", file=sys.stderr)
    if every_chat:
        print("Connected. Storing messages from all chats. Ctrl+C to stop.")
    else:
        print(f"Connected. Storing messages from {len(allow)} chat(s). Ctrl+C to stop.")


def on_message(_: NewClient, ev: MessageEv) -> None:
    src = ev.Info.MessageSource
    alt = src.RecipientAlt if src.IsFromMe else src.SenderAlt
    save(
        phone_jid(Jid2String(src.Chat), Jid2String(alt) if alt.User else ""),
        ev.Info.ID,
        src.Sender.User,
        "me" if src.IsFromMe else ev.Info.Pushname,
        ev.Info.Timestamp,
        ev.Message,
    )


def on_history(_: NewClient, ev: HistorySyncEv) -> None:
    for conv in ev.Data.conversations:
        chat = phone_jid(conv.ID, conv.pnJID)
        if chat not in allow:
            name = conv.name or conv.displayName
            if not wanted(chat, name):
                continue
            remember_chat(chat, name)
        for hm in conv.messages:
            w = hm.message
            # In direct chats the participant is empty: the sender is the chat itself unless it is you.
            sender = (w.key.participant or w.participant or ("" if w.key.fromMe else chat)).split("@")[0]
            save(chat, w.key.ID, sender, "me" if w.key.fromMe else w.pushName,
                 w.messageTimestamp, w.message)


def main() -> None:
    global db, allow, names, every_chat
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="print your groups and contacts, then exit")
    args = parser.parse_args()

    old = Path(__file__).resolve().parent.parent / "data"  # where a source checkout kept it before
    if (old / "session.db").exists() and not SESSION_DB.exists():
        sys.exit(f"Your data now lives in {HOME}. Move {old} there, and chats.txt too if you have one.")
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    client = NewClient(str(SESSION_DB), props=DEVICE)

    if args.list:
        if not is_linked(SESSION_DB):
            sys.exit("Not linked yet. Run `whatsapp-chats` first: linking there saves the past history "
                     "WhatsApp sends once.")

        @client.event(ConnectedEv)
        def _(c: NewClient, __: ConnectedEv) -> None:
            for jid, name in joined_chats(c):
                print(f"{jid}  # {name}")
            print(f"\nCopy the lines you want into {CHATS_FILE}")
            c.stop()

        client.connect()
        return

    allow, names = load_allowlist()
    every_chat = reads_all_chats()
    if every_chat:
        print(f"{CHATS_FILE} is missing or empty: storing messages from ALL your chats, groups and direct.")
    else:
        print(f"Reading the chats listed in {CHATS_FILE}")
    if not is_linked(SESSION_DB):
        print("Linking: after scanning, keep this running. Past history can take several minutes to arrive.")
    db = open_writable()
    threading.Thread(target=document_worker, args=(client,), daemon=True).start()

    client.event(ConnectedEv)(on_connected)
    client.event(MessageEv)(on_message)
    client.event(HistorySyncEv)(on_history)
    client.connect()


if __name__ == "__main__":
    main()
