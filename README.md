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

One command, `whatsapp-chats`, does three jobs:

| Command | What it does |
|---|---|
| `whatsapp-chats` | The bridge. Links to your WhatsApp as a device (like WhatsApp Web) and, while it runs, saves the text of messages and attached documents from your chosen chats. Messages from other chats are never stored. |
| `whatsapp-chats server` | The MCP server Claude starts. Four read-only tools: `list_chats`, `search_messages`, `get_messages`, `get_document`. Opens the database read-only and checks `chats.txt` on every call. |
| `whatsapp-chats folder` | Prints your data folder, which holds `chats.txt` and `data/`. |

Your data folder is:

- Windows: `%LOCALAPPDATA%\MLTeam WhatsApp Chats`
- macOS: `~/Library/Application Support/MLTeam WhatsApp Chats`
- Linux: `~/.local/share/MLTeam WhatsApp Chats`

Set `WHATSAPP_CHATS_HOME` to use another folder.

## Setup

Works on Windows, macOS and Linux. On Windows, type the commands in **PowerShell**
(Start menu > "PowerShell").

1. Install [uv](https://docs.astral.sh/uv/), which installs Python and this tool for you.

   Windows:
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   macOS or Linux:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
   Then close the terminal and open a new one.

2. Install this tool:
   ```bash
   uv tool install https://github.com/mlteam-ai/mlteam-whatsapp-chats-mcp/archive/refs/heads/main.zip
   ```

3. Optional, **before linking**: choose which chats to read. Skip this to read **all** your chats:
   Claude then sees every group and direct chat. To choose, create `chats.txt` in your data folder
   (see above; `whatsapp-chats folder` prints it) with one chat per line: the exact name of a group
   or contact (for example `Family Trip`), or its ID. For direct chats an ID such as
   `905551234567@s.whatsapp.net` is more reliable, because contact names can take a while to sync.
   See [chats.example.txt](chats.example.txt).
   WhatsApp sends past messages only once, at linking, and only chats chosen then are kept.

4. Start the bridge, link your WhatsApp, and leave the window open:
   ```bash
   whatsapp-chats
   ```
   Scan the QR code: WhatsApp on your phone > Settings > Linked devices > Link a device.
   It appears there as "MLTeam WhatsApp Chats Reader (read-only)".
   If your phone cannot read the QR code, give the terminal a dark background and run `whatsapp-chats`
   again: on a light background the code shows with its colors reversed. On macOS Terminal, choose
   Settings > Profiles > "Pro".
   The bridge asks for up to one year of past messages. Your phone decides how much it sends,
   and it can take several minutes, so keep the bridge and your phone online meanwhile.
   After that, it stores new messages whenever it runs. Run `whatsapp-chats` again after a restart.

   Optional, once linked: `whatsapp-chats --list` prints your groups and contacts with their IDs
   (`120363012345678901@g.us`, `905551234567@s.whatsapp.net`). IDs keep working if a name changes.

5. Connect it to Claude, once.

   Claude Code:
   ```bash
   claude mcp add -s user whatsapp-chats -- whatsapp-chats server
   ```
   Claude Desktop: Settings > Developer > Edit Config, add this to `claude_desktop_config.json`,
   then restart Claude Desktop:
   ```json
   {
     "mcpServers": {
       "whatsapp-chats": { "command": "whatsapp-chats", "args": ["server"] }
     }
   }
   ```
   If Claude Desktop cannot find the command, use its full path: run `where.exe whatsapp-chats`
   (Windows) or `which whatsapp-chats` (macOS) and put that in `"command"` (on Windows, write `\\` for each `\`).

6. Ask Claude, for example: "What did the Family Trip group decide about flights?"

## Updating

```bash
uv tool install --force --upgrade --reinstall https://github.com/mlteam-ai/mlteam-whatsapp-chats-mcp/archive/refs/heads/main.zip
```
Then restart the bridge and Claude. This also updates the WhatsApp library, which fixes most
connection errors such as `Client outdated (405)`.

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
  backfilled. To get them, start over: remove the device in WhatsApp > Linked devices, delete `data/`
  in your data folder, and run `whatsapp-chats` again.
- **Remove a chat:** delete its line. Claude stops seeing it immediately. To delete its stored messages:
  ```bash
  sqlite3 data/messages.db "DELETE FROM messages WHERE chat_jid='<chat JID>'"
  ```

## Staying safe

- **Keep `data/` private.** `data/session.db` is a logged-in WhatsApp session: anyone with it can act as
  your linked device. Never share or sync it (for example with OneDrive or iCloud). It is created owner-only.
- **Revoke any time:** WhatsApp > Settings > Linked devices > remove "MLTeam WhatsApp Chats Reader".
- **Untrusted files:** documents come from other people. They are parsed in a separate
  process with a 120-second limit and never opened or run otherwise.
- **Prompt injection:** anyone who messages you can write text aimed at Claude. These tools cannot send
  anything, but Claude's other tools (shell, web fetch) could leak data if Claude followed such text.
  Keep approving those tools yourself while using this server, or deny them.
- **Only include chats you are comfortable sending to Claude.** Messages Claude reads go to Anthropic
  like the rest of your conversation.
- **If it stops connecting** (for example `Client outdated (405)`), update: see "Updating" above.

## Development

```bash
git clone https://github.com/mlteam-ai/mlteam-whatsapp-chats-mcp.git
cd mlteam-whatsapp-chats-mcp
uv run whatsapp-chats            # run the bridge from the checkout
uv run test_selfcheck.py         # offline self-check
```
The self-check feeds fake messages through the bridge and server without connecting to WhatsApp.
CI runs it on Windows, macOS and Linux on every push.

Moving from an older checkout that kept `data/` and `chats.txt` in the repo folder: move both into
your data folder (`uv run whatsapp-chats folder`). The bridge stops with this reminder until you do.

Report security issues privately: see [SECURITY.md](SECURITY.md).
