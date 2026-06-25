"""
MCP shared state — global locks, semaphores, events, and circuit breakers.
Extracted to avoid circular imports between mcp_handler, mcp_config, and mcp_client.
"""
from __future__ import annotations
import asyncio
import threading
from typing import Any, Dict, Optional
from python.helpers.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

# ─── Global locks and singleton state ───────────────────────────────
_mcp_config_lock = threading.Lock()
_mcp_config_instance = None
_mcp_config_initialized = False
_mcp_config_str = ""
_mcp_config_init_in_progress = False

# ─── Per-event-loop semaphores and events ───────────────────────────
_mcp_config_semaphores: Dict[int, asyncio.Semaphore] = {}
_mcp_config_ready_events: Dict[int, asyncio.Event] = {}

# ─── Per-server locks and clients ───────────────────────────────────
_mcp_server_locks: Dict[str, threading.Lock] = {}
_mcp_server_clients: Dict[str, Any] = {}

# ─── Circuit breakers ──────────────────────────────────────────────
_mcp_circuit_breakers: Dict[str, CircuitBreaker] = {}
_mcp_server_refresh_cooldown: Dict[str, float] = {}

# Circuit breaker config for MCP servers
_mcp_circuit_breaker_config = CircuitBreakerConfig(
    failure_threshold=5,           # Open after 5 failures (tolerant of transient errors)
    success_threshold=1,           # Close after 1 success (quick recovery)
    timeout=10.0,                  # Base timeout: 10s
    half_open_max_calls=1,         # Only 1 test call in half-open
    use_exponential_backoff=True,  # Enable adaptive backoff
    min_timeout=5.0,               # Floor: 5s (faster recovery attempts)
    max_timeout=120.0,             # Ceiling: 2 minutes (was 10min — too long for multi-agent)
    backoff_multiplier=2.0,        # Double each failure (was triple — too aggressive)
    jitter_factor=0.3,             # ±30% random variation (better distributed load)
)


def get_mcp_init_semaphore() -> asyncio.Semaphore:
    """Get or create an asyncio.Semaphore bound to the current event loop."""
    loop = asyncio.get_running_loop()
    loop_id = id(loop)
    if loop_id not in _mcp_config_semaphores:
        _mcp_config_semaphores[loop_id] = asyncio.Semaphore(2)
    return _mcp_config_semaphores[loop_id]


def get_mcp_ready_event() -> asyncio.Event:
    """Get or create an asyncio.Event bound to the current event loop."""
    loop = asyncio.get_running_loop()
    loop_id = id(loop)
    if loop_id not in _mcp_config_ready_events:
        # Initialize as set if not in progress
        event = asyncio.Event()
        if not _mcp_config_init_in_progress:
            event.set()
        _mcp_config_ready_events[loop_id] = event
    return _mcp_config_ready_events[loop_id]


def get_server_lock(server_name: str) -> threading.Lock:
    """Get a lock for a specific server (for client lifecycle management)."""
    global _mcp_server_locks
    if server_name not in _mcp_server_locks:
        _mcp_server_locks[server_name] = threading.Lock()
    return _mcp_server_locks[server_name]


def get_server_client(server_name: str) -> Optional[Any]:
    """Get the cached client for a server."""
    global _mcp_server_clients
    return _mcp_server_clients.get(server_name)


def set_server_client(server_name: str, client):
    """Set the cached client for a server."""
    global _mcp_server_clients
    _mcp_server_clients[server_name] = client

