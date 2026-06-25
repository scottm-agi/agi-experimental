from __future__ import annotations
import threading

_status = "initializing"
_errors = []
_lock = threading.Lock()

def set_status(status: str):
    global _status
    with _lock:
        _status = status

def add_error(error: str):
    global _errors
    with _lock:
        _errors.append(error)

def get_status():
    with _lock:
        return _status, _errors
