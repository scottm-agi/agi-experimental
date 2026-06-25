from __future__ import annotations
from python.helpers.api import ApiHandler, Request, Response
from python.helpers import dotenv_manager as dotenv, runtime
from python.helpers.tunnel_manager import TunnelManager
import asyncio
import aiohttp


class TunnelProxy(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        return await process(input)

async def process(input: dict) -> dict | Response:
    # Get configuration from environment
    tunnel_api_port = (
        runtime.get_arg("tunnel_api_port")
        or int(dotenv.get_dotenv_value("TUNNEL_API_PORT", 0))
        or 55520
    )

    # first verify the service is running using async HTTP client:
    service_ok = False
    try:
        # Use aiohttp for non-blocking async requests with short timeout
        timeout = aiohttp.ClientTimeout(total=1)  # 1 second timeout for health check
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"http://localhost:{tunnel_api_port}/",
                json={"action": "health"}
            ) as response:
                if response.status == 200:
                    service_ok = True
    except asyncio.TimeoutError:
        service_ok = False
    except Exception as e:
        service_ok = False

    # forward this request to the tunnel service if OK
    if service_ok:
        try:
            # Use a reasonable timeout for the actual request
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"http://localhost:{tunnel_api_port}/",
                    json=input
                ) as response:
                    return await response.json()
        except Exception as e:
            return {"error": str(e)}
    else:
        # forward to API handler directly
        from python.api.tunnel import process as local_process
        return await local_process(input)
