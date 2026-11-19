"""Read-only WhatsApp bridge.

Links to your account as a device and stores the text of messages, and of
attached documents (PDF, DOCX, XLSX, PPTX, plain text), from the groups listed
in groups.txt (every group if the file is empty) into data/messages.db. Document files are not kept, only their
text. It never sends messages and opens no network ports.

    uv run bridge.py          # link (first run) and store messages from groups.txt
    uv run bridge.py --list   # after linking: print your groups and their JIDs

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

from common import DATA_DIR, GROUPS_FILE, SESSION_DB, is_allowed, load_allowlist, open_writable, reads_all_groups
from extract import SUPPORTED

# Asked for at link time; the phone may send less.
HISTORY_DAYS = 365

EXTRACTOR = Path(__file__).resolve().parent / "extract.py"
MAX_DOC_BYTES = 25 * 1024 * 1024
MAX_DOC_CHARS = 2_000_000
EXTRACT_TIMEOUT = 120  # seconds

DEVICE = DeviceProps(
    os="MLTeam WhatsApp Groups Reader (read-only)",  # shown in WhatsApp > Linked devices
    platformType=DeviceProps.SAFARI,
    requireFullSync=True,
    historySyncConfig=DeviceProps.HistorySyncConfig(fullSyncDaysLimit=HISTORY_DAYS),
)

db = None
db_lock = threading.Lock()
allow: set[str] = set()  # JIDs to store; group names from groups.txt resolve into it
names: set[str] = set()
every_group = False  # groups.txt is empty: store all groups
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


def remember_group(jid: str, name: str) -> None:
    allow.add(jid)
    with db_lock:
        db.execute("INSERT OR REPLACE INTO chats VALUES (?,?)", (jid, name))
        db.commit()


def save(chat: str, msg_id: str, sender: str, name: str, ts: int, message) -> None:
    if chat not in allow:
        if not (every_group and chat.endswith("@g.us")):
            return
        remember_group(chat, "")  # joined while running; name fills in on next connect
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
    for g in client.get_joined_groups():
        jid, name = Jid2String(g.JID), g.GroupName.Name
        if every_group or is_allowed(jid, name, allow, names):
            remember_group(jid, name)
            matched.setdefault(name.casefold(), []).append(jid)
    for n in sorted(names):
        hits = matched.get(n, [])
        if not hits:
            print(f"warning: no group named {n!r} (names must match exactly)", file=sys.stderr)
        elif len(hits) > 1:
            print(f"warning: {len(hits)} groups named {n!r}; reading all. Use JIDs to pick one.", file=sys.stderr)
    print(f"Connected. Storing messages from {len(allow)} group(s). Ctrl+C to stop.")


def on_message(_: NewClient, ev: MessageEv) -> None:
    src = ev.Info.MessageSource
    save(
        Jid2String(src.Chat),
        ev.Info.ID,
        src.Sender.User,
        "me" if src.IsFromMe else ev.Info.Pushname,
        ev.Info.Timestamp,
        ev.Message,
    )


def on_history(_: NewClient, ev: HistorySyncEv) -> None:
    for conv in ev.Data.conversations:
        if conv.ID not in allow:
            if not (every_group and conv.ID.endswith("@g.us")) and not is_allowed(conv.ID, conv.name, set(), names):
                continue
            remember_group(conv.ID, conv.name)
        for hm in conv.messages:
            w = hm.message
            sender = (w.key.participant or w.participant).split("@")[0]
            save(conv.ID, w.key.ID, sender, "me" if w.key.fromMe else w.pushName,
                 w.messageTimestamp, w.message)


def main() -> None:
    global db, allow, names, every_group
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="print joined groups and exit")
    args = parser.parse_args()

    DATA_DIR.mkdir(mode=0o700, exist_ok=True)
    client = NewClient(str(SESSION_DB), props=DEVICE)

    if args.list:
        if not is_linked(SESSION_DB):
            sys.exit(f"Not linked yet. Add group names to {GROUPS_FILE.name} and run `uv run bridge.py` "
                     "first: linking there saves the past history WhatsApp sends once.")

        @client.event(ConnectedEv)
        def _(c: NewClient, __: ConnectedEv) -> None:
            for g in c.get_joined_groups():
                print(f"{Jid2String(g.JID)}  # {g.GroupName.Name}")
            print(f"\nCopy the lines you want into {GROUPS_FILE.name}.")
            c.stop()

        client.connect()
        return

    if not GROUPS_FILE.exists():
        sys.exit(f"No {GROUPS_FILE.name}. Copy groups.example.txt to {GROUPS_FILE.name}, then add the "
                 "exact names of the groups to read, or leave it empty to read every group.")
    allow, names = load_allowlist()
    every_group = reads_all_groups()
    if every_group:
        print(f"{GROUPS_FILE.name} is empty: storing messages from ALL your groups.")
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
