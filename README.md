# 🤖 Notes-Buddy: Telegram Google Drive Controller

**Notes-Buddy** is a high-performance Telegram bot that serves as a bridge between your chat interface and Google Drive. It allows for seamless file management, multi-user authentication, and advanced features like bulk-zipping directly from your mobile or desktop Telegram client.

---

## ✨ Features & Capabilities

### 📂 File Management
- **Full Navigation:** Browse your Drive folders with an intuitive inline-keyboard interface.
- **Search:** Instant keyword-based search across your entire Drive.
- **Metadata:** View detailed file information including size, MIME type, and creation date.
- **Organization:** Create folders, rename files, and move content between directories.
- **Favorites:** Mark important files for quick access via a dedicated favorites menu.
- **Recent:** Quickly access files you've recently modified.

### ⬆️ Upload & Download
- **Direct Upload:** Send any file or document to the bot to upload it directly to your current Drive directory.
- **Smart Downloads:** 
  - Small files (< 45 MB) are sent directly to your chat.
  - Large files (> 45 MB) provide direct Google Drive links to bypass Telegram's bot API limits.
- **Bulk Zipping:** Search for files by keyword and bundle them into a single ZIP archive on-the-fly.

### 🛡️ Security & Privacy
- **Per-User OAuth:** Every user authenticates with their own Google account. The bot never sees your password.
- **Secure Storage:** Access tokens are stored in a local SQLite database, encrypted at the OS level if configured.
- **Session Control:** Use `/logout` at any time to wipe your session and revoke access tokens.

---

## 🛠️ Tech Stack

- **[Python 3.11+](https://www.python.org/)** — Core logic and async orchestration.
- **[python-telegram-bot v20](https://python-telegram-bot.org/)** — Modern async framework for the Telegram interface.
- **[FastAPI](https://fastapi.tiangolo.com/)** — Lightweight web server for handling OAuth2 callbacks.
- **[Google Drive API v3](https://developers.google.com/drive/api/v3/about-sdk)** — Direct cloud interaction.
- **[SQLite](https://sqlite.org/)** — Zero-config database for persisting user sessions and favorites.

---

## 📁 Project Architecture

```text
Notes-Buddy/
├── bot/
│   ├── commands.py      # /command handlers
│   ├── callbacks.py     # Inline button interactions
│   ├── ui.py            # Keyboard & UI layouts
│   └── formatter.py     # Message string builders
├── drive/
│   ├── auth.py          # Google OAuth2 implementation
│   └── drive_service.py # Drive API wrappers
├── services/
│   ├── zip_service.py   # In-memory archive creation
│   └── parser.py        # Input processing helpers
├── db/
│   └── models.py        # Database schema & CRUD
├── main.py              # Application entry point
└── credentials.json     # (Required) Google API credentials
```

---

## 🔐 Security Considerations

1. **Token Persistence:** Tokens are stored in `bot_data.db`. While this allows for session persistence across bot restarts, ensure the environment where the bot is hosted is secure.
2. **Redirect URIs:** The OAuth callback server runs on port `8000`. In production, this should be behind a reverse proxy (like Nginx) with HTTPS enabled to prevent token interception.
3. **Environment Variables:** Sensitive data like `TELEGRAM_BOT_TOKEN` should be managed via `.env` files or system environment variables. Never commit these to version control.
4. **App Permissions:** The bot requests `https://www.googleapis.com/auth/drive` scope. You can modify `drive/auth.py` to use `drive.file` for more restricted access (only files created by the bot).

---

## 🚀 Roadmap & Next Steps

- [x] **Phase 1-4:** Core bot functionality, Drive integration, and file management.
- [ ] **Phase 5: Enhanced UI/UX** — Implement breadcrumb navigation and improved loading states.
- [ ] **Phase 6: Shared Drive Support** — Ability to browse and manage Google Shared Drives.
- [ ] **Phase 7: AI Integration** — Natural language search (e.g., "Find the PDF about physics I uploaded last week").
- [ ] **Phase 8: Multi-Account Support** — Allow users to switch between multiple Google accounts.
- [ ] **Phase 9: Background Transfers** — Handle larger file uploads via chunked streaming to avoid timeouts.

---

## 🎮 What You Can Do Right Now

1. **Connect:** Run `/start` and click the login link to link your Google Drive.
2. **Organize:** Use `/create_folder` to tidy up your root directory.
3. **Migrate:** Send a file to the bot from your phone and see it appear instantly in your Drive.
4. **Archive:** Try `/zip exam` to bundle all your "exam" related documents into one file for easy sharing.
5. **Clean Up:** Use `/browse` to find old files and delete them directly from the interface.

---

## ⚙️ Quick Setup

1. **Clone:** `git clone https://github.com/fidhaaann/Notes-Buddy-`
2. **Install:** `pip install -r requirements.txt`
3. **Credentials:** Place `credentials.json` from Google Cloud Console in the root.
4. **Environment:** Create a `.env` file with `TELEGRAM_BOT_TOKEN`.
5. **Run:** `python main.py`

---
*Developed with ❤️ for the Developer Community.*