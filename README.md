# WhatsApp groups MCP (read-only)

Let Claude answer questions from the WhatsApp groups you choose.

- **Your own account:** each user links their own WhatsApp by scanning a QR code. No shared server
  and no shared credentials.
- **Your own groups:** you list the groups Claude may read, by name or ID, in `groups.txt`.
- **Read-only:** it cannot send messages, opens no network ports, and all data stays on your machine.

> Not affiliated with WhatsApp or Meta. This uses an unofficial client
> ([neonize](https://github.com/krypton-byte/neonize) / [whatsmeow](https://github.com/tulir/whatsmeow)),
> which breaks WhatsApp's Terms of Service. Read-only use is low risk, but your account could be banned.
> Use at your own risk.

## How it works

| Part | What it does |
|---|---|
| `bridge.py` | Links to your WhatsApp as a device (like WhatsApp Web). Saves the text of messages from the groups in `groups.txt` to `data/messages.db`. Messages from other groups are never stored. |
| `server.py` | MCP server with three read-only tools: `list_groups`, `search_messages`, `get_messages`. Opens the database read-only and checks `groups.txt` on every call. |

## Setup

Requires [uv](https://docs.astral.sh/uv/) and macOS, Linux or Windows.

1. Clone and enter the repo:
   ```bash
   git clone https://github.com/mlteam-ai/mlteam-whatsapp-groups-mcp.git
   cd mlteam-whatsapp-groups-mcp
   ```

2. Link your WhatsApp and list your groups:
   ```bash
   uv run bridge.py --list
   ```
   Scan the QR code: WhatsApp on your phone > Settings > Linked devices > Link a device.
   It appears there as "MLTeam WhatsApp Groups Reader (read-only)".

3. Create your group list:
   ```bash
   cp groups.example.txt groups.txt
   ```
   Add one group per line: its exact name (`Family Trip`) or its ID (`120363012345678901@g.us`).
   IDs keep working if the group is renamed.

4. Start the bridge and leave it running:
   ```bash
   uv run bridge.py
   ```
   Right after linking, WhatsApp sends some recent history; older messages are not available.
   After that, only messages that arrive while the bridge runs are stored.

5. Add the MCP server to Claude Code (run in the repo folder):
   ```bash
   claude mcp add whatsapp-groups -- uv --directory "$PWD" run server.py
   ```
   For Claude Desktop, add to `claude_desktop_config.json`:
   ```json
   {
     "mcpServers": {
       "whatsapp-groups": {
         "command": "uv",
         "args": ["--directory", "/absolute/path/to/mlteam-whatsapp-groups-mcp", "run", "server.py"]
       }
     }
   }
   ```

6. Ask Claude, for example: "What did the Family Trip group decide about flights?"

## Changing groups

- **Add a group:** add a line to `groups.txt` and restart the bridge. Earlier messages are not backfilled.
- **Remove a group:** delete its line. Claude stops seeing it immediately. To delete its stored messages:
  ```bash
  sqlite3 data/messages.db "DELETE FROM messages WHERE chat_jid='<group JID>'"
  ```

## Staying safe

- **Keep `data/` private.** `data/session.db` is a logged-in WhatsApp session: anyone with it can act as
  your linked device. Never commit, share or sync it. It is git-ignored and created owner-only.
- **Revoke any time:** WhatsApp > Settings > Linked devices > remove "MLTeam WhatsApp Groups Reader".
- **Prompt injection:** any group member can write text aimed at Claude. These tools cannot send
  anything, but Claude's other tools (shell, web fetch) could leak data if Claude followed such text.
  Keep approving those tools yourself while using this server, or deny them.
- **Only list groups you are comfortable sending to Claude.** Messages Claude reads go to Anthropic
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
