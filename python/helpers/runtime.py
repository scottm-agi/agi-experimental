from __future__ import annotations
import argparse
import inspect
import secrets
from pathlib import Path
from typing import TypeVar, Callable, Awaitable, Union, overload, cast
from python.helpers import dotenv_manager as dotenv, rfc, files
import asyncio
import threading
import queue
import sys
import os

T = TypeVar("T")
R = TypeVar("R")

parser = argparse.ArgumentParser()
args = {}
dockerman = None
runtime_id = None


def initialize():
    global args
    if args:
        return
    parser.add_argument("--port", type=int, default=None, help="Web UI port")
    parser.add_argument("--http-port", type=int, default=None, help="HTTP redirect port (DEPRECATED - use --https-port)")
    parser.add_argument("--https-port", type=int, default=None, help="HTTPS port")
    parser.add_argument("--host", type=str, default=None, help="Web UI host")
    parser.add_argument(
        "--cloudflare_tunnel",
        type=bool,
        default=False,
        help="Use cloudflare tunnel for public URL",
    )
    parser.add_argument(
        "--development", type=bool, default=False, help="Development mode"
    )

    known, unknown = parser.parse_known_args()
    args = vars(known)
    for arg in unknown:
        if "=" in arg:
            key, value = arg.split("=", 1)
            key = key.lstrip("-")
            args[key] = value


def get_arg(name: str):
    global args
    return args.get(name, None)


def has_arg(name: str):
    global args
    return name in args


def is_dockerized() -> bool:
    """Check if the application is running in a dockerized/production environment."""
    dockerized_arg = get_arg("dockerized")
    is_dockerized_arg = False
    if isinstance(dockerized_arg, bool):
        is_dockerized_arg = dockerized_arg
    elif isinstance(dockerized_arg, str):
        is_dockerized_arg = dockerized_arg.lower() == "true"

    return (
        is_dockerized_arg or 
        os.environ.get("AGIX_DEV_MODE", "").lower() == "true" or
        os.path.exists("/.dockerenv") or
        os.environ.get("RAILWAY_ENVIRONMENT") is not None or
        os.environ.get("RAILWAY_STATIC_URL") is not None
    )


def is_development() -> bool:
    """Check if the application is running in local development mode (not dockerized)."""
    # If explicitly in dev mode via environment variable, it's development
    if os.environ.get("AGIX_DEV_MODE", "").lower() == "true":
        return True
    
    # If explicitly dockerized or on Railway (production-like), and NOT forced into dev mode, it's NOT development
    if is_dockerized():
        return False

    # Otherwise fallback to argument or default to true for safety in local environments
    return get_arg("development") is True


def get_local_url():
    if is_dockerized():
        return "host.docker.internal"
    return "127.0.0.1"


def get_runtime_id() -> str:
    return get_persistent_id()


def get_persistent_id() -> str:
    # 1. Try dedicated file in persistent storage first (avoids .env pollution)
    possible_paths = [
        Path("/agix/data/runtime_id"),
        Path("data/runtime_id"),
    ]
    
    for p in possible_paths:
        try:
            if p.is_file():
                content = p.read_text().strip()
                if content:
                    return content
        except (OSError, ValueError):
            pass

    # 2. Check for legacy .env value
    id = dotenv.get_dotenv_value("AGIX_PERSISTENT_RUNTIME_ID")
    if id:
        # Migrate to file if possible
        _save_persistent_id_to_file(id)
        return id

    # 3. Generate new ID
    id = secrets.token_hex(16)
    _save_persistent_id_to_file(id)
    return id


def _save_persistent_id_to_file(id: str):
    # Try to save to persistent paths
    paths = [Path("/agix/data/runtime_id"), Path("/agix/data/runtime_id"), Path("data/runtime_id")]
    for p in paths:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(id)
            return True
        except OSError:
            pass
    return False


@overload
async def call_development_function(
    func: Callable[..., Awaitable[T]], *args, **kwargs
) -> T: ...


@overload
async def call_development_function(func: Callable[..., T], *args, **kwargs) -> T: ...


async def call_development_function(
    func: Union[Callable[..., T], Callable[..., Awaitable[T]]], *args, **kwargs
) -> T:
    if is_development():
        url = _get_rfc_url()
        password = _get_rfc_password()
        # Normalize path components to build a valid Python module path across OSes
        module_path = Path(
            files.deabsolute_path(func.__code__.co_filename)
        ).with_suffix("")
        module = ".".join(module_path.parts)  # __module__ is not reliable
        result = await rfc.call_rfc(
            url=url,
            password=password,
            module=module,
            function_name=func.__name__,
            args=list(args),
            kwargs=kwargs,
        )
        return cast(T, result)
    else:
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        else:
            return func(*args, **kwargs)  # type: ignore


async def handle_rfc(rfc_call: rfc.RFCCall):
    return await rfc.handle_rfc(rfc_call=rfc_call, password=_get_rfc_password())


def _get_rfc_password() -> str:
    # 1. Try dedicated file in persistent storage first
    possible_paths = [
        Path("/agix/data/rfc_password"),
        Path("/agix/data/rfc_password"),
        Path("data/rfc_password"),
    ]
    
    for p in possible_paths:
        try:
            if p.is_file():
                content = p.read_text().strip()
                if content:
                    return content
        except (OSError, ValueError):
            pass

    # 2. Check for .env value
    password = dotenv.get_dotenv_value(dotenv.KEY_RFC_PASSWORD)
    if not password and is_dockerized():
        # Fallback to env var which might be set in docker-compose/Railway
        password = os.environ.get("RFC_PASSWORD")
    
    if password:
        # Migrate to file if possible
        _save_rfc_password_to_file(password)
        return password

    if not password:
        raise Exception("No RFC password, cannot handle RFC calls.")
    return password


def _save_rfc_password_to_file(password: str):
    # Try to save to persistent paths
    paths = [Path("/agix/data/rfc_password"), Path("/agix/data/rfc_password"), Path("data/rfc_password")]
    for p in paths:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(password)
            return True
        except OSError:
            pass
    return False


def _get_rfc_url() -> str:
    from python.helpers import settings
    set = settings.get_settings()
    url = set["rfc_url"]
    if not "://" in url:
        url = "http://" + url
    if url.endswith("/"):
        url = url[:-1]
    url = url + ":" + str(set["rfc_port_http"])
    url += "/agi/rfc"
    return url


def call_development_function_sync(
    func: Union[Callable[..., T], Callable[..., Awaitable[T]]], *args, **kwargs
) -> T:
    # run async function in sync manner
    result_queue = queue.Queue()

    def run_in_thread():
        result = asyncio.run(call_development_function(func, *args, **kwargs))
        result_queue.put(result)

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join(timeout=30)  # wait for thread with timeout

    if thread.is_alive():
        raise TimeoutError("Function call timed out after 30 seconds")

    result = result_queue.get_nowait()
    return cast(T, result)


def get_web_ui_port():
    # Prioritize standard PORT env var (used by Railway, Cloud Run, etc.)
    port = os.environ.get("PORT")
    if port:
        try:
            return int(port)
        except ValueError:
            pass
            
    web_ui_port = (
        get_arg("port") or int(dotenv.get_dotenv_value("WEB_UI_PORT", 0)) or 5000
    )
    return web_ui_port


def get_http_redirect_port():
    http_port = (
        get_arg("http_port") or int(dotenv.get_dotenv_value("HTTP_REDIRECT_PORT", 0)) or None
    )
    return http_port


def get_https_port():
    https_port = (
        get_arg("https_port") or int(dotenv.get_dotenv_value("HTTPS_PORT", 0)) or None
    )
    return https_port


def get_tunnel_api_port():
    tunnel_api_port = (
        get_arg("tunnel_api_port")
        or int(dotenv.get_dotenv_value("TUNNEL_API_PORT", 0))
        or 55520
    )
    return tunnel_api_port


def get_platform():
    return sys.platform


def is_windows():
    return get_platform() == "win32"


def get_terminal_executable():
    if is_windows():
        return "powershell.exe"
    else:
        return "/bin/bash"
