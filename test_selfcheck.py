"""Offline self-check: fake events -> bridge -> DB -> MCP tools. Run: uv run test_selfcheck.py"""
import tempfile
from pathlib import Path
from types import SimpleNamespace

import common

tmp = Path(tempfile.mkdtemp())
common.DATA_DIR = tmp
common.MESSAGES_DB = tmp / "messages.db"
common.CHATS_FILE = tmp / "chats.txt"
common.OLD_GROUPS_FILE = tmp / "groups.txt"
common.CHATS_FILE.write_text(
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
def contact(jid, name):
    return SimpleNamespace(JID=group(jid, "").JID,
                           Info=SimpleNamespace(FullName=name, FirstName="", PushName="", BusinessName=""))


fake_client = SimpleNamespace(
    get_joined_groups=lambda: [group("111@g.us", "Work"), group("222@g.us", "family trip"), group("999@g.us", "Secret")],
    contact=SimpleNamespace(get_all_contacts=lambda: [contact("905551110000@s.whatsapp.net", "Ali Veli")]))
bridge.Jid2String = lambda j: f"{j.User}@{j.Server}"
bridge.on_connected(fake_client, None)
assert bridge.allow == {"111@g.us", "222@g.us"}, bridge.allow


def live(chat, msg_id, text, ts_ms, alt=""):
    ev = MessageEv()
    src = ev.Info.MessageSource
    src.Chat.User, src.Chat.Server = chat.split("@")
    if alt:
        src.SenderAlt.User, src.SenderAlt.Server = alt.split("@")
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
groups = {g["jid"]: g for g in server.list_chats()}
assert groups.keys() == {"111@g.us", "222@g.us", "333@g.us"}, groups
assert groups["222@g.us"]["name"] == "family trip"

# Removing entries from groups.txt hides them immediately, by JID and by name.
common.CHATS_FILE.write_text("history only\n")
assert server.get_messages("111@g.us") == [] and server.get_messages("222@g.us") == []
assert len(server.search_messages("friday")) == 1

# Server connection really is read-only.
try:
    common.open_readonly().execute("DELETE FROM messages")
    raise AssertionError("read-only DB accepted a write")
except Exception as e:
    assert "readonly" in str(e), e

# Linking check: no file, file without device table, file with a paired device.
import sqlite3
session = tmp / "session.db"
assert not bridge.is_linked(session)
sqlite3.connect(session).close()
assert not bridge.is_linked(session)
with sqlite3.connect(session) as c:
    c.execute("CREATE TABLE whatsmeow_device (jid TEXT)")
assert not bridge.is_linked(session)
with sqlite3.connect(session) as c:
    c.execute("INSERT INTO whatsmeow_device VALUES ('1@s.whatsapp.net')")
assert bridge.is_linked(session)
assert bridge.DEVICE.requireFullSync and bridge.DEVICE.historySyncConfig.fullSyncDaysLimit == 365

# --- Documents: extraction, download flow, server tools ---
import io
import zipfile
from openpyxl import Workbook
import extract


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


DOCX = zip_bytes({"word/document.xml": '<w:document><w:body><w:p><w:r><w:t>Budget total</w:t></w:r>'
                  '<w:r><w:t xml:space="preserve"> is 42 &amp; final</w:t></w:r></w:p>'
                  '<w:p><w:r><w:t>Second line</w:t></w:r></w:p></w:body></w:document>'})
PPTX = zip_bytes({"ppt/slides/slide2.xml": "<p:sld><a:p><a:t>Slide two</a:t></a:p></p:sld>",
                  "ppt/slides/slide10.xml": "<p:sld><a:p><a:t>Slide ten</a:t></a:p></p:sld>"})
wb = Workbook(); wb.active.title = "Costs"; wb.active.append(["item", "eur"]); wb.active.append(["hotel", 300])
buf = io.BytesIO(); wb.save(buf); XLSX = buf.getvalue()
stream = b"BT /F1 24 Tf 72 700 Td (Hello PDF budget) Tj ET"
objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
pdf, offsets = b"%PDF-1.4\n", []
for i, o in enumerate(objs, 1):
    offsets.append(len(pdf)); pdf += b"%d 0 obj\n" % i + o + b"\nendobj\n"
xref = len(pdf)
pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)

assert extract.extract("a.docx", DOCX) == "Budget total is 42 & final\nSecond line"
assert extract.extract("a.pptx", PPTX) == "[slide 1]\nSlide two\n\n[slide 2]\nSlide ten"
assert extract.extract("a.XLSX", XLSX) == "[sheet Costs]\nitem\teur\nhotel\t300"
assert "Hello PDF budget" in extract.extract("a.pdf", pdf)
assert extract.extract("notes.txt", "çay ı".encode()) == "çay ı"
for bad in ("a.exe", "noext"):
    try:
        extract.extract(bad, b"x"); raise AssertionError("unsupported type accepted")
    except ValueError:
        pass

# Bridge flow, extraction in a real subprocess as in production.
common.CHATS_FILE.write_text("111@g.us\n")
FILES = {"plan.docx": DOCX, "costs.pdf": pdf, "broken.pdf": b"%PDF-not really", "app.exe": b"MZ"}
fake = SimpleNamespace(download_any=lambda m: FILES.get(m.documentMessage.fileName))
for i, name in enumerate([*FILES, "expired.docx"]):
    ev = MessageEv()
    ev.Info.MessageSource.Chat.User, ev.Info.MessageSource.Chat.Server = "111", "g.us"
    ev.Info.ID, ev.Info.Timestamp = f"D{i}", 1763600000 + i
    ev.Message.documentMessage.fileName = name
    ev.Message.documentMessage.fileLength = len(FILES.get(name, b""))
    bridge.on_message(None, ev)
# History sync wraps captioned documents; they must still be stored and queued.
hs = HistorySyncEv()
w = hs.Data.conversations.add(ID="111@g.us", name="Work").messages.add().message
w.key.ID, w.messageTimestamp = "D9", 1763600100
w.message.documentWithCaptionMessage.message.documentMessage.fileName = "costs.pdf"
w.message.documentWithCaptionMessage.message.documentMessage.caption = "updated costs"
bridge.on_history(None, hs)
big = MessageEv(); big.Info.ID = "BIG"; big.Message.documentMessage.fileName = "huge.pdf"
big.Message.documentMessage.fileLength = bridge.MAX_DOC_BYTES + 1
assert bridge.documents.qsize() == 6, bridge.documents.qsize()
while not bridge.documents.empty():
    bridge.process_document(fake, *bridge.documents.get())
bridge.process_document(fake, "111@g.us", "BIG", big.Message)

docs = {r[0]: r[1:] for r in bridge.db.execute("SELECT id, text, error FROM documents")}
assert docs["D0"] == ("Budget total is 42 & final\nSecond line", ""), docs["D0"]
assert "Hello PDF budget" in docs["D1"][0] and docs["D9"][0] == docs["D1"][0]
assert docs["D2"][1].startswith("could not read file"), docs["D2"]
assert docs["D3"][1] == "unsupported file type .exe"
assert docs["D4"][1] == "download failed, media may have expired"
assert docs["BIG"][1] == "larger than 25 MB"
assert bridge.db.execute("SELECT text FROM messages WHERE id='D9'").fetchone()[0] == "[document costs.pdf] updated costs"

hits = server.search_messages("pdf budget")
assert {h["id"] for h in hits} == {"D1", "D9"}, hits
assert "Hello PDF budget" in hits[0]["document_snippet"] and hits[0]["chat_jid"] == "111@g.us"
listed = {m["id"]: m for m in server.get_messages("111@g.us")}
assert listed["D0"]["document"].startswith("text available") and listed["D3"]["document"].startswith("unreadable")
assert "document" not in listed["A1"]
doc = server.get_document("111@g.us", "D0", max_chars=1000)
assert doc["text"].startswith("Budget total") and "next_offset" not in doc
long = "x" * 2500
bridge.db.execute("INSERT INTO documents VALUES ('111@g.us','L1','long.txt',?, '')", (long,)); bridge.db.commit()
p1 = server.get_document("111@g.us", "L1", max_chars=1000)
p3 = server.get_document("111@g.us", "L1", offset=2000, max_chars=1000)
assert p1["next_offset"] == 1000 and len(p3["text"]) == 500 and "next_offset" not in p3
# Allowlist applies to documents too.
bridge.db.execute("INSERT INTO documents VALUES ('999@g.us','S1','secret.txt','secret','')"); bridge.db.commit()
assert "error" in server.get_document("999@g.us", "S1")

# Direct chats: by contact name, by phone JID when the chat uses a LID, and in history sync.
common.CHATS_FILE.write_text("ali veli\n905559990000@s.whatsapp.net\n")
bridge.allow, bridge.names = common.load_allowlist()
bridge.on_connected(fake_client, None)
assert bridge.allow == {"905559990000@s.whatsapp.net", "905551110000@s.whatsapp.net"}, bridge.allow
live("905551110000@s.whatsapp.net", "DM1", "Sunday lunch?", 1763700000000)
live("123456@lid", "DM2", "Sunday works", 1763700000000, alt="905559990000@s.whatsapp.net")
live("905557770000@s.whatsapp.net", "DM3", "Sunday spam", 1763700000000)
live("111@g.us", "A9", "Sunday in an unlisted group", 1763700000000)
hs = HistorySyncEv()
conv = hs.Data.conversations.add(ID="905551110000@s.whatsapp.net", name="Ali Veli")
w = conv.messages.add().message
w.key.ID, w.messageTimestamp, w.message.conversation = "DMH", 1763600000, "Sunday last week"
bridge.on_history(None, hs)
hits = {h["id"]: h for h in server.search_messages("sunday")}
assert hits.keys() == {"DM1", "DM2", "DMH"}, hits
assert hits["DMH"]["sender"] == "905551110000" and hits["DM1"]["chat"] == "Ali Veli"
assert hits["DM2"]["chat_jid"] == "905559990000@s.whatsapp.net"

# Missing or empty chats.txt: every chat is stored and shown, but not status updates.
common.CHATS_FILE.write_text("# read everything\n")
assert common.reads_all_chats()
bridge.allow, bridge.names = common.load_allowlist()
bridge.every_chat = True
bridge.on_connected(fake_client, None)
assert bridge.allow == {"111@g.us", "222@g.us", "999@g.us", "905551110000@s.whatsapp.net"}, bridge.allow
live("999@g.us", "X2", "Saturday plan", 1763600000000)
live("444@g.us", "N1", "Saturday in a group joined while running", 1763600000000)
live("905557770000@s.whatsapp.net", "DM4", "Saturday direct message", 1763600000000)
live("status@broadcast", "ST1", "Saturday status", 1763600000000)
assert {h["chat_jid"] for h in server.search_messages("saturday")} == {
    "999@g.us", "444@g.us", "905557770000@s.whatsapp.net"}
assert server.get_document("999@g.us", "S1")["text"] == "secret"
common.CHATS_FILE.unlink()
assert common.reads_all_chats() and len(server.list_chats()) == 7, server.list_chats()

# An old groups.txt is not silently ignored.
common.OLD_GROUPS_FILE.write_text("Family Trip\n")
try:
    common.load_allowlist()
    raise AssertionError("old groups.txt ignored")
except RuntimeError as e:
    assert "chats.txt" in str(e)

print("self-check OK")
