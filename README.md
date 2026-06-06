# Telegram Userbot Manager

Quick starter scaffold for a Telegram userbot manager. Features:
- Management bot handles account connection (phone/code/2FA) and saves user sessions.
- Plugin-based userbot features loadable from `plugins/`.

Setup

1. Copy `.env.example` to `.env` and fill `BOT_TOKEN`, `API_ID`, `API_HASH`.
2. Create a virtualenv and install requirements:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Run

```powershell
python bot_manager.py
```

Files of interest
- `bot_manager.py`: Management bot and session handling.
- `plugins/`: Add plugin modules here. See `plugins/sample.py`.
- `sessions.json`: Stored sessions (auto-created).

AI prompt template (use this when asking an AI to add features)

"""
Project: Telegram userbot manager using Telethon + python-telegram-bot.
Plugin interface: each plugin is a python module in `plugins/` exposing `__help__` dict with keys `commands` (list) and `description` (str), and an async `run(client, args)` function which receives a Telethon `TelegramClient` and a string `args`, and returns a string result.
Example plugin provided in `plugins/sample.py`.
"""
