"""
Simple utility plugin that exposes the .ping command.
"""

__help__ = {
    'commands': ['ping'],
    'description': 'Responds with Pong to verify the bot is alive.'
}

async def run(client, args: str):
    return 'Pong!'
