"""
Utility plugin that exposes the .runtime command.
"""

__help__ = {
    'commands': ['.runtime'],
    'description': 'Return bot runtime and current UTC timestamp.'
}

import time
from datetime import datetime

START_TIME = time.time()

async def run(client, args: str):
    uptime_seconds = time.time() - START_TIME
    hours, remainder = divmod(int(uptime_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    runtime_text = f'Bot uptime: {hours}h {minutes}m {seconds}s.'
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    return f'{runtime_text}\nCurrent UTC time: {now}'
