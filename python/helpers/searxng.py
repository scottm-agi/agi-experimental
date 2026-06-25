from __future__ import annotations
import aiohttp
import os
from python.helpers import runtime

# Default local SearxNG URL
DEFAULT_URL = "http://localhost:55510/search"

async def search(query:str):
    if runtime.is_dockerized():
        return await _search(query)
    return await runtime.call_development_function(_search, query=query)

async def _search(query:str):
    # Use environment variable if set (Issue #381)
    url = os.environ.get("SEARXNG_URL", DEFAULT_URL)
    
    async def _do_post(trust: bool):
        async with aiohttp.ClientSession(trust_env=trust) as session:
            # Common proxy headers to assist SearXNG trust (Issue #645)
            headers = {
                "X-Forwarded-For": "127.0.0.1",
                "X-Real-IP": "127.0.0.1",
                "User-Agent": "Mozilla/5.0 (AGIX-Bot; +https://agix.com)"
            }
            data = {"q": query, "format": "json"}
            async with session.post(url, data=data, headers=headers) as response:
                if response.status != 200:
                    text = await response.text()
                    raise Exception(f"SearxNG error {response.status}: {text[:200]}")
                data = await response.json()
                return data.get("results", [])

    import asyncio
    try:
        # Resilience Strategy: Try with proxy trust first (Issue #645)
        return await _do_post(trust=True)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        # Fallback: Disable proxy trust for direct connection (bypasses misconfigured proxies)
        return await _do_post(trust=False)


def search_sync(query: str):
    import asyncio
    return asyncio.run(search(query))
