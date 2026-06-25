from __future__ import annotations
from abc import abstractmethod
import socket
import struct
import json
import threading
import asyncio
from collections import defaultdict
from functools import wraps
from typing import Union, TypedDict, Dict, Any, Callable
from attr import dataclass
from flask import Request, Response, Flask, session, request, redirect, url_for, send_file, jsonify
from python.agent import AgentContext
from python.initialize import initialize_agent
from python.helpers.print_style import PrintStyle
from python.helpers.errors import format_error
from werkzeug.serving import make_server
import os

UI_DEBUG = os.environ.get("UI_DEBUG", "false").lower() == "true"


def _safe_json_default(obj):
    """RCA-347b: Safe JSON default for objects that leak through API boundaries.
    
    Post F-1 fix, monologue() can return Response objects (from python.helpers.tool)
    which are not JSON serializable. This converts them to their .message string.
    """
    if hasattr(obj, 'message'):
        return getattr(obj, 'message', '') or ''
    raise TypeError(f'Object of type {obj.__class__.__name__} is not JSON serializable')


Input = dict
Output = Union[Dict[str, Any], Response, TypedDict]  # type: ignore

def is_loopback_address(address):
    loopback_checker = {
        socket.AF_INET: lambda x: struct.unpack("!I", socket.inet_aton(x))[0]
        >> (32 - 8)
        == 127,
        socket.AF_INET6: lambda x: x == "::1",
    }
    address_type = "hostname"
    try:
        socket.inet_pton(socket.AF_INET6, address)
        address_type = "ipv6"
    except socket.error:
        try:
            socket.inet_pton(socket.AF_INET, address)
            address_type = "ipv4"
        except socket.error:
            address_type = "hostname"

    if address_type == "ipv4":
        return loopback_checker[socket.AF_INET](address)
    elif address_type == "ipv6":
        return loopback_checker[socket.AF_INET6](address)
    else:
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                r = socket.getaddrinfo(address, None, family, socket.SOCK_STREAM)
            except socket.gaierror:
                return False
            for family, _, _, _, sockaddr in r:
                if not loopback_checker[family](sockaddr[0]):
                    return False
        return True

def requires_loopback(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_loopback_address(request.remote_addr):
            return Response("Access denied.", 403, {})
        return f(*args, **kwargs)

    return decorated

def requires_loopback_async(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        if not is_loopback_address(request.remote_addr):
            return Response("Access denied.", 403, {})
        return await f(*args, **kwargs)

    return decorated

class ApiHandler:
    _context_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
    _global_lock = threading.Lock()

    def __init__(self, app: Flask, thread_lock: threading.Lock):
        self.app = app
        # thread_lock is now handled per-context via _get_context_lock

    @classmethod
    def requires_loopback(cls) -> bool:
        return False

    @classmethod
    def requires_api_key(cls) -> bool:
        return False

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]

    @classmethod
    def requires_csrf(cls) -> bool:
        return cls.requires_auth()

    @abstractmethod
    async def process(self, input: Input, request: Request) -> Output:
        pass

    async def handle_request(self, request: Request) -> Response:
        if UI_DEBUG: print(f"[UI_DEBUG] {self.__class__.__name__}.handle_request ENTER: {request.path}", flush=True)
        try:
            # input data from request based on type
            input_data: Input = {}
            # Always merge query parameters and form data, but let JSON body take precedence if present
            input_data.update(request.args.to_dict())
            input_data.update(request.form.to_dict())
            if request.is_json:
                try:
                    if request.data:
                        body_data = request.get_json()
                        if isinstance(body_data, dict):
                            input_data.update(body_data)
                except Exception as e:
                    PrintStyle().print(f"Error parsing JSON: {str(e)}")


            # process via handler


            # process via handler
            output = await self.process(input_data, request)

            # return output based on type
            if isinstance(output, Response):
                return output
            else:
                # RCA-347b: Use safe encoder to handle Response objects
                # that may leak from monologue() (post F-1 fix).
                response_json = json.dumps(output, default=_safe_json_default)
                return Response(
                    response=response_json, status=200, mimetype="application/json"
                )

            # return exceptions with 500
        except Exception as e:
            import traceback
            traceback.print_exc()  # Always log internally for server-side debugging
            
            # In production, sanitize error responses to prevent info leakage
            from python.helpers import feature_flags
            if feature_flags.is_production_env():
                return Response(
                    response="An internal error occurred. Please try again.",
                    status=500, mimetype="text/plain"
                )
            
            error = format_error(e)
            PrintStyle.error(f"API error: {error}")
            return Response(response=error, status=500, mimetype="text/plain")

    def _get_context_lock(self, ctxid: str) -> threading.Lock:
        """Get or create a lock for a specific context ID."""
        with self._global_lock:
            return self._context_locks[ctxid]

    # get context to run AGIX in
    async def use_context(self, ctxid: str, create_if_not_exists: bool = True):
        if UI_DEBUG: print(f"[UI_DEBUG] use_context ENTER: ctxid={ctxid}", flush=True)
        
        # Use a per-context lock instead of a global one
        # This allows parallel requests for DIFFERENT contexts to proceed
        lock = self._get_context_lock(ctxid or "default")
        
        lock.acquire()
        try:
            # 1. Quick lookup
            if not ctxid:
                first = AgentContext.first()
                if first:
                    AgentContext.use(first.id)
                    return first
            else:
                got = AgentContext.get(ctxid)
                if got:
                    AgentContext.set_current(ctxid)
                    return got

            # 2. Setup
            from python.helpers import localization
            import python.initialize as initialize

            # 3. Prevent resurrection of removed
            if ctxid:
                try:
                    from python.helpers.persist_chat import REMOVED_CONTEXTS
                    if ctxid in REMOVED_CONTEXTS:
                        raise Exception(f"Context {ctxid} is removed.")
                except ImportError:
                    pass

            if not ctxid:
                context = AgentContext(config=initialize.initialize_agent(), set_current=True)
                return context

            # 4. Load or Create
            from python.helpers.persist_chat import load_chat
            context = await load_chat(ctxid)
            
            if context:
                AgentContext.set_current(ctxid)
                return context

            if create_if_not_exists:
                context = AgentContext(config=initialize.initialize_agent(), id=ctxid, set_current=True)
                return context
            else:
                raise Exception(f"Context {ctxid} not found")
        finally:
            lock.release()
