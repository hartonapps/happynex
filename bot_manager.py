import os
import json
import asyncio
import subprocess
import sys
from datetime import datetime

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeExpiredError, PhoneCodeInvalidError

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from plugin_loader import load_plugins
from git_manager import get_git_manager

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID') or 0)
API_HASH = os.getenv('API_HASH')
GIT_REMOTE = os.getenv('GIT_REMOTE', 'origin')
GIT_REMOTE_URL = os.getenv('GIT_REMOTE_URL')
GIT_BRANCH = os.getenv('GIT_BRANCH', 'main')

SESSIONS_FILE = 'sessions.json'
os.makedirs('sessions', exist_ok=True)

# git manager for updates
git_manager = get_git_manager(remote_name=GIT_REMOTE, remote_url=GIT_REMOTE_URL)

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
LIVE_SESSION_CLIENTS = {}

if new_deps:
    PENDING_RESTART = True
    RESTART_REASON.extend([f'New dependency: {pkg}' for pkg in new_deps])
    print('Installed new plugin dependencies:', new_deps)


def format_available_commands():
    if not plugins:
        return 'No plugin commands loaded.'
    lines = []
    for cmd, meta in plugins.items():
        description = meta['help'].get('description', 'No description available.')
        lines.append(f'.{cmd} — {description}')
    return 'Available commands:\n' + '\n'.join(lines)


def register_userbot_handlers(client):
    @client.on(events.NewMessage(outgoing=True, pattern=r'^\.\w+'))
    async def userbot_command_handler(event):
        text = event.raw_text.strip()
        if not text.startswith('.'):
            return

        parts = text[1:].split(None, 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ''

        if cmd == 'commands':
            await event.reply(format_available_commands())
            return

        if cmd not in plugins:
            return

        try:
            result = await plugins[cmd]['run'](client, args)
            if result is None:
                result = 'Done.'
            await event.reply(str(result))
        except Exception as e:
            await event.reply(f'Error running .{cmd}: {e}')


def get_session_status(session_str):
    return 'active' if session_str in LIVE_SESSION_CLIENTS else 'inactive'


async def start_userbot_session(session_str, phone, owner_id):
    if session_str in LIVE_SESSION_CLIENTS:
        return True

    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return False
        register_userbot_handlers(client)
        task = asyncio.create_task(client.run_until_disconnected())
        LIVE_SESSION_CLIENTS[session_str] = {
            'client': client,
            'phone': phone,
            'owner': owner_id,
            'task': task
        }
        print(f'Loaded userbot session for {phone} (owner={owner_id}).')
        return True
    except Exception as e:
        print(f'Failed to start userbot session for {phone}: {e}')
        try:
            await client.disconnect()
        except Exception:
            pass
        return False


async def stop_userbot_session(session_str):
    session_info = LIVE_SESSION_CLIENTS.pop(session_str, None)
    if not session_info:
        return
    client = session_info['client']
    task = session_info.get('task')
    if task and not task.done():
        task.cancel()
    try:
        await client.disconnect()
    except Exception:
        pass


async def start_all_userbot_sessions():
    sessions = load_sessions()
    for owner, user_sessions in sessions.items():
        for session_meta in user_sessions:
            session_str = session_meta.get('session')
            phone = session_meta.get('phone')
            if session_str:
                await start_userbot_session(session_str, phone, owner)


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
    if not git_manager:
        return
    if git_manager.check_remote_updates(remote=GIT_REMOTE, branch=GIT_BRANCH):
        if not UPDATE_AVAILABLE:
            UPDATE_AVAILABLE = True
            UPDATE_FILES = git_manager.get_commit_diff(remote=GIT_REMOTE, branch=GIT_BRANCH)


async def check_updates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global UPDATE_AVAILABLE, UPDATE_FILES
    if not git_manager:
        await update.message.reply_text('Git repository not available in this folder.')
        return

    if git_manager.check_remote_updates(remote=GIT_REMOTE, branch=GIT_BRANCH):
        UPDATE_AVAILABLE = True
        UPDATE_FILES = git_manager.get_commit_diff(remote=GIT_REMOTE, branch=GIT_BRANCH)
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

    changed_files = git_manager.get_commit_diff(remote=GIT_REMOTE, branch=GIT_BRANCH)
    success, message = git_manager.pull_updates(remote=GIT_REMOTE, branch=GIT_BRANCH)
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

    if data == 'resend_code':
        if user.id not in TEMP or TEMP[user.id].get('state') != 'AWAIT_CODE':
            await query.message.reply_text('No active sign-in session.')
            return
        phone = TEMP[user.id].get('phone')
        client = TEMP[user.id].get('client')
        if not client or not phone:
            await query.message.reply_text('Session lost. Start over with /connect.')
            TEMP.pop(user.id, None)
            return
        
        if TEMP[user.id].get('_resend_pending'):
            await query.message.reply_text('⏳ Code resend already in progress. Please wait...')
            return
        
        try:
            TEMP[user.id]['_resend_pending'] = True
            old_hash = TEMP[user.id].get('phone_code_hash', 'NONE')
            print(f"[USER {user.id}] RESEND_CODE: Old hash={old_hash[:20]}...")
            
            result = await client.send_code_request(phone)
            new_hash = result.phone_code_hash
            print(f"[USER {user.id}] RESEND_CODE: New hash={new_hash[:20]}... (changed={old_hash != new_hash})")
            
            TEMP[user.id].update({
                'phone_code_hash': new_hash,
                '_resend_pending': False
            })
            await query.message.reply_text(
                '✅ New code sent! Reply with the code from Telegram now.',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔄 Resend Code', callback_data='resend_code')]])
            )
        except Exception as e:
            print(f"[USER {user.id}] RESEND_CODE ERROR: {type(e).__name__}: {e}")
            await query.message.reply_text(f'Failed to resend: {e}')
            try:
                await client.disconnect()
            except:
                pass
            TEMP.pop(user.id, None)
        return


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # interactive main menu
    keyboard = [
        [InlineKeyboardButton('Connect Account', callback_data='connect')],
        [InlineKeyboardButton('My Sessions', callback_data='sessions')],
        [InlineKeyboardButton('Plugins', callback_data='plugins')],
    ]
    keyboard.append([InlineKeyboardButton('🔄 Check Updates', callback_data='check_updates')])
    if PENDING_RESTART:
        keyboard.append([InlineKeyboardButton('⚠️ Restart Bot', callback_data='restart_bot')])

    msg = 'Welcome — choose an action:'
    if UPDATE_AVAILABLE:
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
            # Note: do not force SMS. Use the standard Telegram code flow.
            result = await client.send_code_request(phone)
            phone_code_hash = result.phone_code_hash
            
            # ✅ DEBUG: Log initial code request
            print(f"[USER {user.id}] AWAIT_PHONE: send_code_request hash={phone_code_hash[:20]}...")
        except Exception as e:
            print(f"[USER {user.id}] AWAIT_PHONE ERROR: {e}")
            await update.message.reply_text(f'Failed to send code: {e}')
            await client.disconnect()
            TEMP.pop(user.id, None)
            return
        
        # ✅ CRITICAL FIX: Keep client alive until sign_in completes
        TEMP[user.id].update({
            'state': 'AWAIT_CODE',
            'phone': phone,
            'client': client,  # ← Client stays connected
            'phone_code_hash': phone_code_hash,
            '_resend_pending': False
        })
        await update.message.reply_text(
            '✅ Code sent!\n\nReply with only the numeric login code (expires in ~5 minutes).\n\nImportant: if the code arrives in the same Telegram app where you are typing it, it may auto-expire. Use another Telegram client/device if possible, and do not forward the entire login message.',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔄 Resend Code', callback_data='resend_code')]])
        )
        return

    if state == 'AWAIT_CODE':
        code = ''.join(ch for ch in text if ch.isdigit())
        if not code:
            await update.message.reply_text(
                '❌ Please send only the numeric login code from Telegram. Do not forward an entire message or include extra text.',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔄 Resend Code', callback_data='resend_code')]])
            )
            return

        client: TelegramClient = TEMP[user.id].get('client')
        phone = TEMP[user.id].get('phone')
        phone_code_hash = TEMP[user.id].get('phone_code_hash')
        if not client or not phone or not phone_code_hash:
            await update.message.reply_text('⚠️ Sign-in state lost. Please start over with /connect.')
            TEMP.pop(user.id, None)
            return

        # ✅ DEBUG: Log state before sign_in
        is_connected = client.is_connected() if hasattr(client, 'is_connected') else 'unknown'
        print(f"[USER {user.id}] AWAIT_CODE: code={code}, hash={phone_code_hash[:20]}..., connected={is_connected}")
        
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            print(f"[USER {user.id}] AWAIT_CODE: sign_in SUCCESS")
        except PhoneCodeExpiredError:
            print(f"[USER {user.id}] AWAIT_CODE: PhoneCodeExpiredError with hash={phone_code_hash[:20]}...")
            await update.message.reply_text(
                '❌ Code expired. The code you entered is no longer valid.\n\nIf you are using the same Telegram app where the code arrived, it may auto-expire. Try using another client/device or request a new code.',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔄 Resend Code', callback_data='resend_code')]])
            )
            return
        except PhoneCodeInvalidError:
            print(f"[USER {user.id}] AWAIT_CODE: PhoneCodeInvalidError")
            await update.message.reply_text(
                '❌ Invalid code. Please send only the numeric code exactly as received, and avoid forwarding the full Telegram login message from the same app.',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔄 Resend Code', callback_data='resend_code')]])
            )
            return
        except SessionPasswordNeededError:
            print(f"[USER {user.id}] AWAIT_CODE: SessionPasswordNeededError (2FA)")
            TEMP[user.id]['state'] = 'AWAIT_PASSWORD'
            await update.message.reply_text('🔐 Two-step password enabled.\n\nSend your 2FA password.')
            return
        except Exception as e:
            # Fallback: inspect exception to detect expired/invalid code even if a different RPC class is raised
            ename = type(e).__name__
            emsg = str(e)
            print(f"[USER {user.id}] AWAIT_CODE ERROR ({ename}): {emsg}")
            if 'PhoneCodeExpired' in ename or 'EXPIRED' in emsg.upper() or 'PHONE_CODE_EXPIRED' in emsg.upper():
                await update.message.reply_text(
                    '❌ Code expired.\n\nIf you copied the code from the same Telegram client where you started this process it may auto-expire — try Resend Code.',
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔄 Resend Code', callback_data='resend_code')]])
                )
                return
            if 'PhoneCodeInvalid' in ename or 'INVALID' in emsg.upper() or 'PHONE_CODE_INVALID' in emsg.upper():
                await update.message.reply_text(
                    '❌ Invalid code. Make sure you pasted the numeric code exactly (don’t forward it verbatim from the same Telegram app).',
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔄 Resend Code', callback_data='resend_code')]])
                )
                return
            # generic fallback: treat as sign-in failure and clean up
            await update.message.reply_text(f'❌ Sign-in failed: {emsg}')
            try:
                await client.disconnect()
            except:
                pass
            TEMP.pop(user.id, None)
            return

        # ✅ SUCCESS - Use client.session.save() instead of StringSession(client.session).save()
        session_str = client.session.save()
        sessions = load_sessions()
        user_sessions = sessions.get(str(user.id), [])
        user_sessions.append({'session': session_str, 'phone': phone, 'created_at': datetime.utcnow().isoformat()})
        sessions[str(user.id)] = user_sessions
        save_sessions(sessions)
        await update.message.reply_text('✅ Account connected and session saved.')

        register_userbot_handlers(client)
        task = asyncio.create_task(client.run_until_disconnected())
        LIVE_SESSION_CLIENTS[session_str] = {
            'client': client,
            'phone': phone,
            'owner': str(user.id),
            'task': task
        }
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
        
        print(f"[USER {user.id}] AWAIT_PASSWORD: attempting 2FA sign_in")
        
        try:
            await client.sign_in(password=password)
            print(f"[USER {user.id}] AWAIT_PASSWORD: sign_in SUCCESS")
        except Exception as e:
            print(f"[USER {user.id}] AWAIT_PASSWORD ERROR: {type(e).__name__}: {e}")
            await update.message.reply_text(f'❌ Password signin failed: {e}')
            try:
                await client.disconnect()
            except:
                pass
            TEMP.pop(user.id, None)
            return
        # ✅ Use client.session.save() instead of StringSession(client.session).save()
        session_str = client.session.save()
        sessions = load_sessions()
        user_sessions = sessions.get(str(user.id), [])
        user_sessions.append({'session': session_str, 'phone': phone, 'created_at': datetime.utcnow().isoformat()})
        sessions[str(user.id)] = user_sessions
        save_sessions(sessions)
        await update.message.reply_text('✅ Account connected and session saved (2FA).')

        register_userbot_handlers(client)
        task = asyncio.create_task(client.run_until_disconnected())
        LIVE_SESSION_CLIENTS[session_str] = {
            'client': client,
            'phone': phone,
            'owner': str(user.id),
            'task': task
        }
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
        session_str = s.get('session')
        status = get_session_status(session_str) if session_str else 'inactive'
        lines.append(f"{i}. {s.get('phone')} — {status} — created {s.get('created_at')}")
    await update.message.reply_text('\n'.join(lines))


async def list_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not plugins:
        await update.message.reply_text('No plugins loaded.')
        return
    lines = []
    for cmd, meta in plugins.items():
        lines.append(f"{cmd} - {meta['help'].get('description','')}")
    await update.message.reply_text('\n'.join(lines))


async def delete_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text('Usage: /delete <session_index>')
        return
    try:
        idx = int(args[0]) - 1
    except ValueError:
        await update.message.reply_text('Session index must be a number from /sessions list.')
        return

    sessions = load_sessions()
    user_sessions = sessions.get(str(update.effective_user.id), [])
    if idx < 0 or idx >= len(user_sessions):
        await update.message.reply_text('Invalid session index.')
        return

    session_meta = user_sessions.pop(idx)
    sessions[str(update.effective_user.id)] = user_sessions
    save_sessions(sessions)

    session_str = session_meta.get('session')
    if session_str:
        await stop_userbot_session(session_str)

    await update.message.reply_text(f'Session {idx + 1} deleted.')


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
    app.add_handler(CommandHandler('delete', delete_session))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(start_all_userbot_sessions())

    if git_manager:
        if getattr(app, 'job_queue', None):
            app.job_queue.run_repeating(check_remote_updates_job, interval=600, first=10)
        else:
            print('Update check skipped: JobQueue is unavailable. Install python-telegram-bot[job-queue] to enable update polling.')

    print('Bot manager running...')
    app.run_polling()


if __name__ == '__main__':
    main()
