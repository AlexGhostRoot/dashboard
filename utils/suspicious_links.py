import re
import aiohttp
from bs4 import BeautifulSoup
import tldextract

SUSPICIOUS_PATTERNS = [
    r'login.*telegram',
    r'verify.*account',
    r'gift.*telegram',
    r'claim.*prize',
]

SUSPICIOUS_DOMAINS = {
    'bit.ly', 'tinyurl.com', 'goo.gl', 't.ly',
    'login-telegram', 'telegram-support', 'tg-premium'
}

async def check_message_for_danger(text: str) -> list[dict]:
    if not text:
        return []

    urls = re.findall(r'(https?://[^\s]+)', text)
    dangers = []

    async with aiohttp.ClientSession() as session:
        for url in urls:
            reason = None
            domain = tldextract.extract(url).registered_domain.lower()

            if domain in SUSPICIOUS_DOMAINS:
                reason = "known suspicious / shortener domain"
            elif any(re.search(p, url.lower()) for p in SUSPICIOUS_PATTERNS):
                reason = "suspicious pattern in URL"
            elif len(url) > 100:
                reason = "very long URL (possible obfuscation)"

            if not reason:
                try:
                    async with session.head(url, timeout=4, allow_redirects=True) as resp:
                        if 'location' in resp.headers:
                            loc = resp.headers['location']
                            if 'telegram' in loc.lower() and 'join' not in loc.lower():
                                reason = "redirects to suspicious telegram link"
                except:
                    pass

            if reason:
                dangers.append({"url": url, "reason": reason})

    return dangers
