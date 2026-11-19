"""Read-only WhatsApp bridge.

Links to your account as a device and stores the text of messages from the
groups listed in groups.txt into data/messages.db. It never sends messages
and opens no network ports.

    uv run bridge.py          # link (first run) and store messages from groups.txt
    uv run bridge.py --list   # after linking: print your groups and their JIDs

WhatsApp sends past history only once, right after linking, so linking happens
in the normal run where that history gets stored.
"""
import argparse
import sqlite3
import sys
import threading
from pathlib import Path

from neonize.client import NewClient
from neonize.events import ConnectedEv, HistorySyncEv, MessageEv
from neonize.proto.waCompanionReg.WAWebProtobufsCompanionReg_pb2 import DeviceProps
from neonize.utils.jid import Jid2String

from common import DATA_DIR, GROUPS_FILE, SESSION_DB, is_allowed, load_allowlist, open_writable

# Asked for at link time; the phone may send less.
HISTORY_DAYS = 365

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
        return
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


def on_connected(client: NewClient, _: ConnectedEv) -> None:
    matched: dict[str, list[str]] = {}
    for g in client.get_joined_groups():
        jid, name = Jid2String(g.JID), g.GroupName.Name
        if is_allowed(jid, name, allow, names):
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
            if not is_allowed(conv.ID, conv.name, set(), names):
                continue
            remember_group(conv.ID, conv.name)
        for hm in conv.messages:
            w = hm.message
            sender = (w.key.participant or w.participant).split("@")[0]
            save(conv.ID, w.key.ID, sender, "me" if w.key.fromMe else w.pushName,
                 w.messageTimestamp, w.message)


def main() -> None:
    global db, allow, names
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

    allow, names = load_allowlist()
    if not allow and not names:
        sys.exit(f"{GROUPS_FILE.name} lists no groups. Copy groups.example.txt to "
                 f"{GROUPS_FILE.name}, then add the exact names of the groups to read.")
    if not is_linked(SESSION_DB):
        print("Linking: after scanning, keep this running. Past history can take several minutes to arrive.")
    db = open_writable()

    client.event(ConnectedEv)(on_connected)
    client.event(MessageEv)(on_message)
    client.event(HistorySyncEv)(on_history)
    client.connect()


if __name__ == "__main__":
    main()
