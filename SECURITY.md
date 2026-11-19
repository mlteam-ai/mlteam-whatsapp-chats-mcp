# Security

Please report vulnerabilities privately through GitHub:
**Security > Report a vulnerability** on this repository. Do not open a public issue.

## Design guarantees

Changes that break any of these are treated as security bugs:

- The bridge never sends WhatsApp messages and opens no network ports.
- The MCP server opens the database read-only and returns only groups listed in `groups.txt`.
- Each user's WhatsApp session, group list and messages stay on their own machine
  (`data/` and `groups.txt` are git-ignored).
