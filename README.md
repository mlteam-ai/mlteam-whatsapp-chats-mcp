# WhatsApp chats MCP (read-only)

Let Claude answer questions from the WhatsApp chats you choose: groups and direct chats.

- **Your own account:** each user links their own WhatsApp by scanning a QR code. No shared server
  and no shared credentials.
- **Your own chats:** you list the chats (groups or contacts) Claude may read, by name or ID, in
  `chats.txt`. Without that file, or with no chats in it, **all** your chats are read.
- **Read-only:** it cannot send messages, opens no network ports, and all data stays on your machine.

> Not affiliated with WhatsApp or Meta. This uses an unofficial client
> ([neonize](https://github.com/krypton-byte/neonize) / [whatsmeow](https://github.com/tulir/whatsmeow)),
> which breaks WhatsApp's Terms of Service. Read-only use is low risk, but your account could be banned.
> Use at your own risk.

## How it works

| Part | What it does |
|---|---|
| `bridge.py` | Links to your WhatsApp as a device (like WhatsApp Web). Saves the text of messages, and of attached documents, from the chats in `chats.txt` (all chats if none are listed) to `data/messages.db`. Messages from other chats are never stored. |
| `extract.py` | Pulls text out of documents. Runs as a separate process with a time limit. |
| `server.py` | MCP server with four read-only tools: `list_chats`, `search_messages`, `get_messages`, `get_document`. Opens the database read-only and checks `chats.txt` on every call. |

## Setup

Requires [uv](https://docs.astral.sh/uv/) and macOS, Linux or Windows.

1. Clone and enter the repo:
   ```bash
   git clone https://github.com/mlteam-ai/mlteam-whatsapp-groups-mcp.git
   cd mlteam-whatsapp-groups-mcp
   ```

2. Choose your chats **before linking**. To read only some chats:
   ```bash
   cp chats.example.txt chats.txt
   ```
   Add the exact name of each group or contact, one per line (for example `Family Trip`).
   For direct chats a phone JID such as `905551234567@s.whatsapp.net` is more reliable, because
   contact names can take a while to sync after linking.
   Skip this step to read **all** your chats: Claude then sees every group and direct chat.
   WhatsApp sends past messages only once, at linking, and only chats chosen then are kept.
   So list every chat you might want now: chats added later get new messages only.

3. Start the bridge, link your WhatsApp, and leave it running:
   ```bash
   uv run bridge.py
   ```
   Scan the QR code: WhatsApp on your phone > Settings > Linked devices > Link a device.
   It appears there as "MLTeam WhatsApp Groups Reader (read-only)".
   The bridge asks for up to one year of past messages. Your phone decides how much it sends,
   and it can take several minutes, so keep the bridge and your phone online meanwhile.
   After that, it stores new messages while it runs.

   Optional, once linked: `uv run bridge.py --list` prints your groups and contacts with their IDs
   (`120363012345678901@g.us`, `905551234567@s.whatsapp.net`). You can use an ID in `chats.txt`
   instead of a name; IDs keep working if the name changes.

4. Add the MCP server to Claude Code (run in the repo folder):
   ```bash
   claude mcp add whatsapp-chats -- uv --directory "$PWD" run server.py
   ```
   For Claude Desktop, add to `claude_desktop_config.json`:
   ```json
   {
     "mcpServers": {
       "whatsapp-chats": {
         "command": "uv",
         "args": ["--directory", "/absolute/path/to/mlteam-whatsapp-groups-mcp", "run", "server.py"]
       }
     }
   }
   ```

5. Ask Claude, for example: "What did the Family Trip group decide about flights?"

## Documents

The bridge downloads documents shared in your chats and keeps their text (not the files),
so Claude can search and read them.

- **Supported:** PDF, DOCX, XLSX, PPTX, and plain text (TXT, CSV, MD, JSON, TSV, LOG).
  Other types, files over 25 MB, and scanned PDFs (images, no text layer) are not read.
- **Old documents:** WhatsApp deletes media from its servers after a while, so some documents
  from past history can no longer be downloaded. Claude sees these as "unreadable".
- **Already linked before this feature?** Documents stored earlier have no text. Only new ones get it,
  unless you start over (see "Add a chat" below).

## Changing chats

- **Add a chat:** add a line to `chats.txt` and restart the bridge. Its earlier messages are not
  backfilled. To get them, start over: remove the device in WhatsApp > Linked devices, delete `data/`,
  and run `uv run bridge.py` again.
- **Remove a chat:** delete its line. Claude stops seeing it immediately. To delete its stored messages:
  ```bash
  sqlite3 data/messages.db "DELETE FROM messages WHERE chat_jid='<chat JID>'"
  ```

## Staying safe

- **Keep `data/` private.** `data/session.db` is a logged-in WhatsApp session: anyone with it can act as
  your linked device. Never commit, share or sync it. It is git-ignored and created owner-only.
- **Revoke any time:** WhatsApp > Settings > Linked devices > remove "MLTeam WhatsApp Groups Reader".
- **Untrusted files:** documents come from other people. They are parsed in a separate
  process with a 120-second limit and never opened or run otherwise.
- **Prompt injection:** anyone who messages you can write text aimed at Claude. These tools cannot send
  anything, but Claude's other tools (shell, web fetch) could leak data if Claude followed such text.
  Keep approving those tools yourself while using this server, or deny them.
- **Only include chats you are comfortable sending to Claude.** Messages Claude reads go to Anthropic
  like the rest of your conversation.
- **If it stops connecting** (for example `Client outdated (405)`), update the WhatsApp library:
  ```bash
  uv lock --upgrade-package neonize && uv sync
  ```

## Development

```bash
uv run test_selfcheck.py
```
Feeds fake messages through the bridge and server without connecting to WhatsApp. CI runs it on every push.

Report security issues privately: see [SECURITY.md](SECURITY.md).
