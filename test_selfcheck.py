"""Offline self-check: fake events -> bridge -> DB -> MCP tools. Run: uv run test_selfcheck.py"""
import tempfile
from pathlib import Path
from types import SimpleNamespace

import common

tmp = Path(tempfile.mkdtemp())
common.DATA_DIR = tmp
common.MESSAGES_DB = tmp / "messages.db"
common.GROUPS_FILE = tmp / "groups.txt"
common.GROUPS_FILE.write_text(
    "# my groups\n111@g.us  # Allowed by JID\n\nFamily Trip  # allowed by name\nhistory only\n"
)

import bridge
import server
from neonize.events import HistorySyncEv, MessageEv

bridge.allow, bridge.names = common.load_allowlist()
assert bridge.allow == {"111@g.us"} and bridge.names == {"family trip", "history only"}
bridge.db = common.open_writable()


def group(jid, name):
    user, server_ = jid.split("@")
    return SimpleNamespace(JID=SimpleNamespace(User=user, Server=server_, RawAgent=0, Device=0, Integrator=0,
                                               IsEmpty=False), GroupName=SimpleNamespace(Name=name))


# Connect: JID entry and case-insensitive name entry resolve; other groups don't.
fake_client = SimpleNamespace(get_joined_groups=lambda: [
    group("111@g.us", "Work"), group("222@g.us", "family trip"), group("999@g.us", "Secret")])
bridge.Jid2String = lambda j: f"{j.User}@{j.Server}"
bridge.on_connected(fake_client, None)
assert bridge.allow == {"111@g.us", "222@g.us"}, bridge.allow


def live(chat, msg_id, text, ts_ms):
    ev = MessageEv()
    src = ev.Info.MessageSource
    src.Chat.User, src.Chat.Server = chat.split("@")
    src.Sender.User = "905551112233"
    ev.Info.ID, ev.Info.Pushname, ev.Info.Timestamp = msg_id, "Ayse", ts_ms
    ev.Message.conversation = text
    bridge.on_message(None, ev)


live("111@g.us", "A1", "Meeting moved to Friday", 1763500000000)  # ms timestamp
live("222@g.us", "B1", "Friday flights booked", 1763510000000)
live("999@g.us", "X1", "Friday secret from non-allowed group", 1763500000000)
ev = MessageEv()
ev.Info.MessageSource.Chat.User, ev.Info.MessageSource.Chat.Server = "111", "g.us"
ev.Info.ID, ev.Info.Timestamp = "A2", 1763450000000
ev.Message.imageMessage.caption = "friday agenda"
bridge.on_message(None, ev)

# History sync: allowed by JID, by name (not seen at connect), and a non-allowed group.
hs = HistorySyncEv()
for chat, name in (("111@g.us", "Work"), ("333@g.us", "History Only"), ("999@g.us", "Secret")):
    conv = hs.Data.conversations.add(ID=chat, name=name)
    w = conv.messages.add().message
    w.key.ID, w.key.participant, w.pushName = "H-" + chat, "905550000000@s.whatsapp.net", "Mehmet"
    w.messageTimestamp = 1763400000  # seconds
    w.message.extendedTextMessage.text = "Old note about Friday"
bridge.on_history(None, hs)

stored = {r[0] for r in bridge.db.execute("SELECT DISTINCT chat_jid FROM messages")}
assert stored == {"111@g.us", "222@g.us", "333@g.us"}, f"wrong groups stored: {stored}"

hits = server.search_messages("friday")
assert len(hits) == 5, hits
assert not any("secret" in h["text"] for h in hits)
assert hits[-1]["sender_name"] == "Mehmet" and hits[-1]["sender"] == "905550000000", hits
assert [m["text"] for m in server.get_messages("111@g.us")] == [
    "Old note about Friday", "[image] friday agenda", "Meeting moved to Friday"]
assert server.get_messages("999@g.us") == []
groups = {g["jid"]: g for g in server.list_groups()}
assert groups.keys() == {"111@g.us", "222@g.us", "333@g.us"}, groups
assert groups["222@g.us"]["name"] == "family trip"

# Removing entries from groups.txt hides them immediately, by JID and by name.
common.GROUPS_FILE.write_text("history only\n")
assert server.get_messages("111@g.us") == [] and server.get_messages("222@g.us") == []
assert len(server.search_messages("friday")) == 1

# Server connection really is read-only.
try:
    common.open_readonly().execute("DELETE FROM messages")
    raise AssertionError("read-only DB accepted a write")
except Exception as e:
    assert "readonly" in str(e), e

print("self-check OK")
