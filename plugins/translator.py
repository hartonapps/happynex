"""
Translation plugin using the DevToolbox AI translation API.
Supports replying to a message or translating inline text.
"""

__requires__ = ['httpx']
__help__ = {
    'commands': ['translate'],
    'description': (
        'Translates text. \n'
        '1. Reply to a message: `.translate fr`\n'
        '2. Inline text: `.translate hello world fr` or `.translate "hello" fr`'
    )
}

API_URL = "https://devtoolbox-api.devtoolbox-api.workers.dev/ai/translate"

async def run(client, args: str):
    import httpx
    import re

    if not args:
        return (
            "⚠️ Missing arguments.\n"
            "• Reply to a message: `.translate fr`\n"
            "• Inline text: `.translate 'text' fr`"
        )

    try:
        # Fetch the most recent message in the active chat to contextually find replies
        async for message in client.iter_messages(None, limit=1):
            reply_to = await message.get_reply_message()
            
            if reply_to and reply_to.text:
                # --- Method 1: Reply Mode ---
                target_lang = args.strip().lower()
                text_to_translate = reply_to.text
            else:
                # --- Method 2: Inline Text Mode ---
                words = args.strip().split()
                if len(words) < 2:
                    return "⚠️ Please provide both the text and a target language code. (e.g., `.translate hello fr`)"
                
                # The last item is assumed to be the language code
                target_lang = words[-1].lower()
                raw_text = " ".join(words[:-1])
                
                # Strip out matching single, double, or curly smart-quotes if present
                text_to_translate = re.sub(r'^[\'""‘’“”]|[\'""‘’“”]$', '', raw_text).strip()

            # --- API Request Execution ---
            payload = {
                "text": text_to_translate,
                "target_lang": target_lang
            }
            
            async with httpx.AsyncClient() as httpx_client:
                response = await httpx_client.post(API_URL, json=payload, timeout=15.0)
                
                if response.status_code == 200:
                    data = response.json()
                    # Fallback checks depending on how your API returns the structured JSON string
                    translated_text = data.get("translated_text") or data.get("text") or str(data)
                    return f"🌐 **Translated ({target_lang.upper()}):**\n{translated_text}"
                
                elif response.status_code in (400, 422):
                    return f"⚠️ API Error: Invalid language code ('{target_lang}') or bad payload structural format."
                else:
                    return f"❌ Translation API returned an error status: {response.status_code}"

    except httpx.RequestError as e:
        return f"❌ Network error connecting to Cloudflare Worker API: {str(e)}"
    except Exception as e:
        return f"❌ An unexpected plugin error occurred: {str(e)}"
