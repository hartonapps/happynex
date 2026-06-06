"""
Sample plugin demonstrating the required interface.
"""

__help__ = {
    'commands': ['echo'],
    'description': 'Echo back provided text.'
}

async def run(client, args: str):
    # This plugin simply returns the args back to the caller.
    return args or 'No text provided.'
