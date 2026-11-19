"""Entry point for the `whatsapp-chats` command; see bridge.py for usage."""
import sys

from .common import HOME


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "server":  # stdout is the MCP channel here: print nothing
        from .server import mcp
        mcp.run()
        return
    sys.stdout.reconfigure(errors="replace")  # Windows consoles can't print every chat name
    if command == "folder":
        print(HOME)
    else:
        from .bridge import main as bridge
        bridge()
