import os
import json
import asyncio
import subprocess
import sys
from datetime import datetime

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from plugin_loader import load_plugins
from git_manager import get_git_manager

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID') or 0)
API_HASH = os.getenv('API_HASH')
ADMIN_ID = int(os.getenv('ADMIN_ID') or 0)

SESSIONS_FILE = 'sessions.json'
os.makedirs('sessions', exist_ok=True)

# git manager for updates
git_manager = get_git_manager()

# load plugins and check for new deps
plugins_result = load_plugins()
if isinstance(plugins_result, tuple):
    plugins, new_deps = plugins_result
else:
    plugins = plugins_result
    new_deps = []

# runtime state for multi-step connect
TEMP = {}
UPDATE_AVAILABLE = False
PENDING_RESTART = False
RESTART_REASON = []
UPDATE_FILES = []

if new_deps:
    PENDING_RESTART = True
    RESTART_REASON.extend([f'New dependency: {pkg}' for pkg in new_deps])
    print('Installed new plugin dependencies:', new_deps)


def load_sessions():
    try:
        with open(SESSIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_sessions(data):
    with open(SESSIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def format_changed_files(changed_files):
    if not changed_files:
        return 'No changed files available.'
    return '\n'.join(f'- {path}' for path in changed_files)


def should_require_restart(changed_files):
    if not changed_files:
        return True
    for path in changed_files:
        if path == 'requirements.txt' or path.startswith('plugins/') or path.startswith('plugin_loader.py'):
            return True
    return False


async def check_remote_updates_job(context):
    global UPDATE_AVAILABLE, UPDATE_FILES
    if not git_manager or not ADMIN_ID:
        return
    if git_manager.check_remote_updates():
        if not UPDATE_AVAILABLE:
            UPDATE_AVAILABLE = True
            UPDATE_FILES = git_manager.get_commit_diff()
            message = 'GitHub updates are available for the bot repository.\n'
            message += format_changed_files(UPDATE_FILES)
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=message)
            except Exception:
                pass


async def check_updates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global UPDATE_AVAILABLE, UPDATE_FILES
    if not git_manager:
        await update.message.reply_text('Git repository not available in this folder.')
        return

    if git_manager.check_remote_updates():
        UPDATE_AVAILABLE = True
        UPDATE_FILES = git_manager.get_commit_diff()
        await update.message.reply_text(
            'Updates are available from the remote repository.\n'
            + format_changed_files(UPDATE_FILES),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Pull updates now', callback_data='pull_updates')]])
        )
        return

    UPDATE_AVAILABLE = False
    UPDATE_FILES = []
    await update.message.reply_text('No updates found on the remote repository.')


async def pull_updates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global UPDATE_AVAILABLE, PENDING_RESTART, RESTART_REASON, UPDATE_FILES
    if not git_manager:
        await update.message.reply_text('Git repository not available in this folder.')
        return

    changed_files = git_manager.get_commit_diff()
    success, message = git_manager.pull_updates()
    if not success:
        await update.message.reply_text(f'Pull failed: {message}')
        return

    UPDATE_AVAILABLE = False
    UPDATE_FILES = changed_files
    needs_restart = should_require_restart(changed_files)
    if needs_restart:
        PENDING_RESTART = True
        RESTART_REASON.append('Repository update')

    requirements_installed = False
    package_message = ''
    if 'requirements.txt' in changed_files:
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'])
            requirements_installed = True
            package_message = '\nDependencies updated from requirements.txt.'
        except subprocess.CalledProcessError:
            package_message = '\nFailed to install requirements.txt automatically. Restart the bot after manual install.'

    response = 'Updates pulled successfully.'
    if needs_restart:
        response += '\nA restart is recommended to apply the latest code changes.'
    if package_message:
        response += package_message

    await update.message.reply_text(response)


async def request_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_ID and update.effective_user.id != ADMIN_ID:
        await update.message.reply_text('Only the configured admin may restart the bot.')
        return

    await update.message.reply_text('Restarting the bot now. Please wait...')
    sys.exit(0)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ''
    user = query.from_user

    if data == 'connect':
        TEMP[user.id] = {'state': 'AWAIT_PHONE'}
        await query.message.reply_text('Send the phone number (international format, e.g. +15551234567).')
        return

    if data == 'sessions':
        sessions = load_sessions()
        user_sessions = sessions.get(str(user.id), [])
        if not user_sessions:
            await query.message.reply_text('No sessions found. Use Connect Account to add one.')
            return
        lines = [f"{i}. {s.get('phone')} — {s.get('created_at')}" for i, s in enumerate(user_sessions, 1)]
        await query.message.reply_text('\n'.join(lines))
        return

    if data == 'plugins':
        if not plugins:
            await query.message.reply_text('No plugins loaded.')
            return
        kb = []
        for cmd, meta in plugins.items():
            kb.append([InlineKeyboardButton(f"{cmd}", callback_data=f'plugin:{cmd}')])
        await query.message.reply_text('Select a plugin command:', reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == 'check_updates':
        await check_updates(update, context)
        return

    if data == 'pull_updates':
        await pull_updates(update, context)
        return

    if data == 'restart_bot':
        await request_restart(update, context)
        return

    if data.startswith('plugin:'):
        cmd = data.split(':', 1)[1]
        sessions = load_sessions()
        user_sessions = sessions.get(str(user.id), [])
        if not user_sessions:
            await query.message.reply_text('No sessions available. Connect an account first.')
            return
        kb = []
        for i, s in enumerate(user_sessions, 1):
            kb.append([InlineKeyboardButton(f"{i}. {s.get('phone')}", callback_data=f'exec|{i-1}|{cmd}')])
        await query.message.reply_text('Choose a session to run the command with:', reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith('exec|'):
        # exec|<session_idx>|<cmd>
        parts = data.split('|', 2)
        if len(parts) < 3:
            await query.message.reply_text('Invalid execution request.')
            return
        _, idx_str, cmd = parts
        try:
            idx = int(idx_str)
        except Exception:
            await query.message.reply_text('Invalid session index.')
            return
        # ask for args
        TEMP[user.id] = {'state': 'AWAIT_CMD_ARGS', 'session_idx': idx, 'cmd': cmd}
        await query.message.reply_text('Send command arguments as a single message, or send /skip to run without arguments.')
        return


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # interactive main menu
    keyboard = [
        [InlineKeyboardButton('Connect Account', callback_data='connect')],
        [InlineKeyboardButton('My Sessions', callback_data='sessions')],
        [InlineKeyboardButton('Plugins', callback_data='plugins')],
    ]
    # add admin-only buttons for owner
    if ADMIN_ID and update.effective_user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton('🔄 Check Updates', callback_data='check_updates')])
        if PENDING_RESTART:
            keyboard.append([InlineKeyboardButton('⚠️ Restart Bot', callback_data='restart_bot')])
    
    msg = 'Welcome — choose an action:'
    if UPDATE_AVAILABLE and ADMIN_ID and update.effective_user.id == ADMIN_ID:
        msg += '\n\n✨ Updates available! Press Check Updates.'
    
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    TEMP[user.id] = {'state': 'AWAIT_PHONE'}
    await update.message.reply_text('Send the phone number (international format, e.g. +15551234567).')


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in TEMP:
        return
    state = TEMP[user.id]['state']
    text = update.message.text.strip()

    if state == 'AWAIT_PHONE':
        phone = text
        # create telethon client for the sign-in flow
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        try:
            await client.send_code_request(phone)
        except Exception as e:
            await update.message.reply_text(f'Failed to send code: {e}')
            await client.disconnect()
            TEMP.pop(user.id, None)
            return
        TEMP[user.id].update({'state': 'AWAIT_CODE', 'phone': phone, 'client': client})
        await update.message.reply_text('Code sent. Please reply with the login code you received.')
        return

    if state == 'AWAIT_CODE':
        code = text
        client: TelegramClient = TEMP[user.id]['client']
        phone = TEMP[user.id]['phone']
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            TEMP[user.id]['state'] = 'AWAIT_PASSWORD'
            await update.message.reply_text('Two-step password is enabled. Send your 2FA password now.')
            return
        except Exception as e:
            await update.message.reply_text(f'Sign-in failed: {e}')
            await client.disconnect()
            TEMP.pop(user.id, None)
            return

        # success
        session_str = StringSession(client.session).save()
        sessions = load_sessions()
        user_sessions = sessions.get(str(user.id), [])
        user_sessions.append({'session': session_str, 'phone': phone, 'created_at': datetime.utcnow().isoformat()})
        sessions[str(user.id)] = user_sessions
        save_sessions(sessions)
        await update.message.reply_text('Account connected and session saved.')
        await client.disconnect()
        TEMP.pop(user.id, None)
        return

    if state == 'AWAIT_CMD_ARGS':
        session_idx = TEMP[user.id].get('session_idx')
        cmd = TEMP[user.id].get('cmd')
        cmd_args = text if text != '/skip' else ''
        sessions = load_sessions()
        user_sessions = sessions.get(str(user.id), [])
        if session_idx is None or session_idx < 0 or session_idx >= len(user_sessions):
            await update.message.reply_text('Invalid session selection.')
            TEMP.pop(user.id, None)
            return
        session_str = user_sessions[session_idx]['session']
        if cmd not in plugins:
            await update.message.reply_text('Unknown command.')
            TEMP.pop(user.id, None)
            return
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await client.connect()
        try:
            result = await plugins[cmd]['run'](client, cmd_args)
            if result is None:
                result = 'Done.'
            await update.message.reply_text(str(result))
        except Exception as e:
            await update.message.reply_text(f'Plugin error: {e}')
        finally:
            await client.disconnect()
            TEMP.pop(user.id, None)
        return

    if state == 'AWAIT_PASSWORD':
        password = text
        client: TelegramClient = TEMP[user.id]['client']
        phone = TEMP[user.id]['phone']
        try:
            await client.sign_in(password=password)
        except Exception as e:
            await update.message.reply_text(f'Password signin failed: {e}')
            await client.disconnect()
            TEMP.pop(user.id, None)
            return
        session_str = StringSession(client.session).save()
        sessions = load_sessions()
        user_sessions = sessions.get(str(user.id), [])
        user_sessions.append({'session': session_str, 'phone': phone, 'created_at': datetime.utcnow().isoformat()})
        sessions[str(user.id)] = user_sessions
        save_sessions(sessions)
        await update.message.reply_text('Account connected and session saved (2FA).')
        await client.disconnect()
        TEMP.pop(user.id, None)
        return


async def list_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sessions = load_sessions()
    user_sessions = sessions.get(str(update.effective_user.id), [])
    if not user_sessions:
        await update.message.reply_text('No sessions found. Use /connect to add one.')
        return
    lines = []
    for i, s in enumerate(user_sessions, 1):
        lines.append(f"{i}. {s.get('phone')} — created {s.get('created_at')}")
    await update.message.reply_text('\n'.join(lines))


async def list_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not plugins:
        await update.message.reply_text('No plugins loaded.')
        return
    lines = []
    for cmd, meta in plugins.items():
        lines.append(f"{cmd} - {meta['help'].get('description','')}")
    await update.message.reply_text('\n'.join(lines))


async def exec_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # usage: /exec <session_index> <command> [args...]
    args = context.args
    if len(args) < 2:
        await update.message.reply_text('Usage: /exec <session_index> <command> [args...]')
        return
    try:
        idx = int(args[0]) - 1
    except ValueError:
        await update.message.reply_text('session_index must be a number from /sessions list.')
        return
    cmd = args[1]
    cmd_args = ' '.join(args[2:]) if len(args) > 2 else ''

    sessions = load_sessions()
    user_sessions = sessions.get(str(update.effective_user.id), [])
    if idx < 0 or idx >= len(user_sessions):
        await update.message.reply_text('Invalid session index.')
        return
    session_str = user_sessions[idx]['session']
    if cmd not in plugins:
        await update.message.reply_text('Unknown command. Use /commands to list available plugin commands.')
        return

    # create telethon client from saved session
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await client.connect()
    try:
        result = await plugins[cmd]['run'](client, cmd_args)
        if result is None:
            result = 'Done.'
        await update.message.reply_text(str(result))
    except Exception as e:
        await update.message.reply_text(f'Plugin error: {e}')
    finally:
        await client.disconnect()


def main():
    if not BOT_TOKEN or not API_ID or not API_HASH:
        print('Please fill .env with BOT_TOKEN, API_ID, API_HASH')
        return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(CommandHandler('connect', connect))
    app.add_handler(CommandHandler('sessions', list_sessions))
    app.add_handler(CommandHandler('commands', list_commands))
    app.add_handler(CommandHandler('exec', exec_command))
    app.add_handler(CommandHandler('check_updates', check_updates))
    app.add_handler(CommandHandler('restart', request_restart))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    if git_manager and ADMIN_ID:
        app.job_queue.run_repeating(check_remote_updates_job, interval=600, first=10)

    print('Bot manager running...')
    app.run_polling()


if __name__ == '__main__':
    main()
