# Telegram Userbot Manager

Quick starter scaffold for a Telegram userbot manager. Features:
- Management bot handles account connection (phone/code/2FA) and saves user sessions.
- Plugin-based userbot features loadable from `plugins/`.

Setup

1. Copy `.env.example` to `.env` and fill `BOT_TOKEN`, `API_ID`, `API_HASH`, `GIT_REMOTE`, and `GIT_BRANCH`.
2. Create a virtualenv and install requirements:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

GitHub update config
- `GIT_REMOTE`: remote name to check and pull from (default `origin`).
- `GIT_BRANCH`: branch name to compare and pull (default `main`).

If your Git repo has a remote set, put those values in `.env`. The bot uses them to detect and pull updates.

This repository tracks a placeholder `.env` file so it is visible and editable in the project.

Run

```powershell
python bot_manager.py
```

Files of interest
- `bot_manager.py`: Management bot and session handling.
- `plugins/`: Add plugin modules here. See `plugins/sample.py`.
- `sessions.json`: Stored sessions (auto-created).

Plugin dependency support
- Plugins may declare required packages with a `__requires__` variable.
- If a plugin needs a package that is not installed, the bot will auto-install it on load and append it to `requirements.txt`.
- Example plugin header:

```python
__requires__ = ['requests']
__help__ = {
    'commands': ['echo'],
    'description': 'Echo back provided text.'
}
```

GitHub update workflow
- The bot can detect new commits on the remote `origin/main` branch if the project is a Git repo.
- If updates are available, the configured bot admin receives a notification.
- Admins can pull updates from the bot UI and the bot will recommend or request a restart if needed.
- If `requirements.txt` changes, the bot will try to install updated dependencies automatically after pulling.

AI prompt template (use this when asking an AI to add features)

"""
Project: Telegram userbot manager using `python-telegram-bot` for the control bot and `Telethon` for userbot sessions.

Structure:
- `bot_manager.py`: main Telegram bot, handles user commands, session storage, and update notifications.
- `plugins/`: plugin modules loaded dynamically.
- `plugin_loader.py`: scans `plugins/`, imports each module, installs `__requires__` dependencies if needed, and registers plugin commands.
- `plugin_manager.py`: dependency helper that installs packages and appends them to `requirements.txt`.

Plugin interface:
- A plugin is a Python module in `plugins/`.
- It should define `__help__ = {'commands': [...], 'description': '...'}.`
- It may declare `__requires__ = ['package-name']` for extra dependencies.
- It must provide `async def run(client, args)` where `client` is a `Telethon` `TelegramClient` and `args` is the command text.

Example plugin:
```python
__requires__ = ['requests']
__help__ = {
    'commands': ['echo'],
    'description': 'Echo back provided text.'
}

async def run(client, args: str):
    return args or 'No text provided.'
```
"""
