# NotesBuddy — Telegram Google Drive Assistant

A professional, terminal-inspired Telegram bot for managing Google Drive files. Browse, download, upload, and organize your cloud storage directly from any Telegram client.

---

## Features

### Navigation
- **Hierarchical Browsing** — View your Drive structure with indexed items (`[1]`, `[1.1]`, `[2]`, etc.)
- **Terminal-Style Traversal** — Navigate with `/cd`, `/pwd`, `/info` like a file system
- **Session Tracking** — Persistent path state with LRU eviction and 24-hour TTL

### File Operations
- **Index-Based Actions** — Download, view metadata, and enter folders using their displayed index
- **Smart Downloads** — Files ≤ 45 MB sent directly; larger files provide Google Drive links
- **Upload Flow** — Upload documents, images, and videos directly to the current folder
- **Bulk ZIP** — Archive matching files into a single download

### Security & Privacy
- **Per-User OAuth** — Each user authenticates with their own Google account
- **PKCE + CSRF Protection** — OAuth flow secured with S256 PKCE and state nonce verification
- **Encrypted Storage** — Fernet encryption for stored tokens (mandatory in production)
- **Token Revocation** — Tokens invalidated at Google on logout
- **Audit Logging** — All destructive operations (delete, rename, move) logged with timestamps
- **Step-up Verification** — Email OTP required for delete/download/upload actions
- **Threat Alerts** — Email alerts for suspicious activity
- **Input Sanitization** — Filename, query, and index validation on all user inputs
- **Rate Limiting** — Per-user cooldowns on expensive operations

---

## Command Reference

| Command | Description |
|---------|-------------|
| `/start` | Welcome screen + OAuth login |
| `/info` | List current directory with indexed items |
| `/cd <n>` | Enter folder by index |
| `/cd` | Go back one level |
| `/pwd` | Print current path |
| `/download <n>` | Download file by index |
| `/more <n>` | View detailed file metadata |
| `/search <q>` | Search all files by keyword |
| `/upload` | Enter upload mode (documents/images/videos) |
| `/zip <q>` | Download matching files as ZIP |
| `/rename <n> <new>` | Rename by index |
| `/delete <n>` | Delete a file by index |
| `/move <f> <d>` | Move file to folder by index |
| `/mkdir <name>` | Create a new folder |
| `/logout` | Disconnect Google Drive (revokes token) |
| `/email <addr>` | Set email for security alerts |
| `/verify <otp>` | Verify a sensitive action |
| `/clear` | Clear recent chat messages |
| `/menu` | Show main menu |
| `/help` | Show command reference |
| `/tool` | Show keywords & abilities |

---

## Architecture

```text
Notes-Buddy/
├── bot/
│   ├── commands.py      # /command handlers (terminal-style navigation)
│   ├── callbacks.py     # Inline button interactions
│   ├── handlers.py      # Handler registration + file upload
│   ├── ui.py            # Keyboard layouts & inline buttons
│   ├── nav.py           # LRU session state & hierarchical index mapping
│   └── formatter.py     # Professional message templates
├── drive/
│   ├── auth.py          # OAuth2 with PKCE, CSRF, & token revocation
│   └── drive_service.py # Drive API wrappers with audit logging
├── services/
│   ├── zip_service.py   # In-memory archive creation
│   └── parser.py        # Input processing helpers
├── db/
│   └── models.py        # SQLite schema, encryption, CRUD, audit log
├── templates/
│   └── success.html     # OAuth redirect page
├── main.py              # Entry point (FastAPI + Telegram bot)
└── credentials.json     # Google API credentials (not committed)
```

---

## Tech Stack

- **Python 3.11+** — Async orchestration
- **python-telegram-bot 22.7** — Telegram interface
- **FastAPI 0.136 + Uvicorn 0.47** — OAuth callback server
- **Google Drive API v3** — Cloud file management
- **SQLite (WAL mode)** — Zero-config session persistence with encrypted tokens

---

## Security

| Layer | Protection |
|---|---|
| **OAuth** | PKCE (S256) + CSRF nonce + 10-minute state expiry |
| **Tokens** | Fernet-encrypted in SQLite; mandatory in production |
| **Revocation** | Tokens revoked at Google on `/logout` |
| **Audit** | All deletes, renames, and moves logged with user ID + timestamp |
| **Scope** | `auth/drive` (required for full file management; see `drive/auth.py`) |
| **Input** | Filename sanitization, query escaping, index validation, rate limiting |
| **Transport** | HTTPS required for production OAuth redirect URI |
| **Cleanup** | Periodic background task purges expired OAuth states every 30 minutes |

> **Production Requirement:** Set `TOKEN_ENCRYPTION_KEY` — the bot will refuse to start on Railway without it.
> Generate with: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
> Step-up email OTP can be enabled with `STEPUP_VERIFICATION_ENABLED=true` (disabled by default).

---

## Quick Setup

1. **Clone:** `git clone https://github.com/fidhaaann/Notes-Buddy-`
2. **Install:** `pip install -r requirements.txt`
3. **Credentials:** Place `credentials.json` from Google Cloud Console in the root (or set `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` env vars)
4. **Environment:** Create `.env` from `.env.example` — set `TELEGRAM_BOT_TOKEN`, `OAUTH_REDIRECT_URI`, and `TOKEN_ENCRYPTION_KEY`
5. **Run:** `python main.py`

---

## Roadmap

- [x] Core bot functionality & Drive integration
- [x] Terminal-style navigation with hierarchical indexing
- [x] Professional UX redesign
- [x] Security audit & hardening (PKCE, audit logging, token revocation)
- [ ] Shared Drive support
- [ ] Natural language search
- [ ] Multi-account switching
- [ ] Chunked streaming for large uploads

---
*Developed with ❤️ for the Developer Community.*
