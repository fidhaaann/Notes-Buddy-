# 🤖 Telegram Google Drive Control Bot

A Telegram bot that acts as a full control interface for your Google Drive — upload, download, search, rename, delete, and bulk-ZIP files, all from chat.

---

## ✨ Features

| Command | Description |
|---|---|
| `/start` | Welcome message & command list |
| `/login` | Connect your Google account via OAuth |
| `/logout` | Disconnect & delete stored tokens |
| `/folders` | List folders in current location |
| `/open <folder>` | Navigate into a folder |
| `/list` | List files in current folder |
| `/get <filename>` | Download a file from Drive |
| `/search <keyword>` | Search all files by keyword |
| `/rename <old> <new>` | Rename a file |
| `/delete <filename>` | Permanently delete a file |
| `/zip <keyword>` | Bundle matching files into a ZIP |
| *(send any file)* | Upload directly to Google Drive |

---

## 🛠️ Tech Stack

- **Python 3.11+**
- **python-telegram-bot v20** — async Telegram bot framework
- **FastAPI + Uvicorn** — OAuth callback web server
- **Google Drive API v3** — cloud storage
- **Google OAuth 2.0** — per-user authentication
- **SQLite** — token & file metadata storage (zero infrastructure needed)
- **zipfile** — built-in Python ZIP support

---

## 📁 Project Structure

```
project/
├── bot/
│   ├── __init__.py
│   ├── commands.py      # All /command handlers
│   └── handlers.py      # Handler registration + file upload
├── drive/
│   ├── __init__.py
│   ├── auth.py          # OAuth flow (get URL, exchange code, refresh)
│   └── drive_service.py # All Drive API calls
├── services/
│   ├── __init__.py
│   ├── zip_service.py   # In-memory ZIP creation
│   └── parser.py        # Argument parsing & formatting helpers
├── db/
│   ├── __init__.py
│   └── models.py        # SQLite schema & CRUD helpers
├── main.py              # Entry point (bot + OAuth server)
├── requirements.txt
├── .env.example
└── implementation.md
```

---

## ⚙️ Setup

### 1. Clone & create virtual environment

```bash
git clone <repo-url>
cd <repo-folder>
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create your Telegram Bot

1. Open Telegram → search **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **bot token**

### 4. Set up Google Drive API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable **Google Drive API**
4. Go to **Credentials → Create Credentials → OAuth 2.0 Client ID**
5. Application type: **Web application**
6. Add Authorized redirect URI: `http://localhost:8000/oauth/callback`
7. Download `credentials.json` and place it in the project root

### 5. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your TELEGRAM_BOT_TOKEN
```

Or export directly:

```bash
# Windows PowerShell
$env:TELEGRAM_BOT_TOKEN = "your-token-here"

# macOS/Linux
export TELEGRAM_BOT_TOKEN="your-token-here"
```

### 6. Run

```bash
python main.py
```

The bot starts polling Telegram, and the OAuth server listens on `http://localhost:8000`.

---

## 🔐 Authentication Flow

```
User → /login
Bot  → Sends Google OAuth consent URL
User → Opens link, grants permission
Google → Redirects to http://localhost:8000/oauth/callback
Server → Stores token in SQLite
Bot  → Confirms: "Authorization successful!"
```

Every subsequent command transparently loads and auto-refreshes the token.

---

## 💾 Database Schema

```sql
users:  user_id | telegram_id | token | refresh_token
files:  file_id | name        | type  | uploaded_at
```

---

## ⚠️ Limitations

- Telegram file size limit: **50 MB** per file (bot API restriction)
- Google Drive API free-tier quotas apply
- ZIP downloads are built in-memory — avoid zipping very large file sets

---

## 🔐 Security Notes

- Tokens are stored locally in SQLite — never commit `bot_data.db` or `credentials.json`
- `.gitignore` already excludes both
- Use `/logout` to wipe stored tokens at any time
- Run behind HTTPS in production (use a reverse proxy like nginx)

---

## 🚀 Development Phases

- [x] Phase 1 — Bot + OAuth + Upload
- [x] Phase 2 — List + Download
- [x] Phase 3 — Rename + Delete + Search
- [x] Phase 4 — ZIP + Bulk download
- [ ] Phase 5 — UI Improvements (inline keyboards)
- [ ] Phase 6 — AI-powered search (optional)