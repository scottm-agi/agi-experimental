"""
Model metadata and context window resolution.
"""
import logging
import os
import json
from typing import Any, Dict

logger = logging.getLogger(__name__)

MODEL_METADATA = {
    "grok-41-fast": {"context_window": 262144},
    "grok-4.1-fast": {"context_window": 2000000},
    "grok-4.1-fast-zdr": {"context_window": 2000000},
    "x-ai/grok-4.1-fast": {"context_window": 2000000},
    "x-ai/grok-code-fast-1": {"context_window": 262144},
    "grok-4-fast": {"context_window": 2000000},
    "grok-3": {"context_window": 1000000},
    "grok-2": {"context_window": 128000},
    "grok-beta": {"context_window": 128000},
    "gpt-5": {"context_window": 400000},
    "gpt-5-pro": {"context_window": 400000},
    "gpt-5.1": {"context_window": 400000},
    "o3": {"context_window": 400000},
    "o3-mini": {"context_window": 400000},
    "gpt-4.5": {"context_window": 128000},
    "gpt-4o": {"context_window": 128000},
    "claude-4": {"context_window": 200000},
    "claude-4.5": {"context_window": 1000000},
    "claude-4.5-opus": {"context_window": 1000000},
    "anthropic/claude-opus-4.6": {"context_window": 1000000},
    "claude-4.5-sonnet": {"context_window": 200000},
    "claude-3-5-sonnet": {"context_window": 200000},
    "claude-3-opus": {"context_window": 200000},
    "gemini-3-pro": {"context_window": 2000000},
    "gemini-3-flash": {"context_window": 1048576},
    "gemini-2.5-pro": {"context_window": 1000000},
    "gemini-2.0-pro": {"context_window": 1000000},
    "gemini-2.0-flash": {"context_window": 1000000},
    "gemini-1.5-pro": {"context_window": 1000000},
    "llama-4": {"context_window": 128000},
    "llama-3.3-70b": {"context_window": 128000},
    "llama-3.1-405b": {"context_window": 128000},
    "deepseek-v4": {"context_window": 128000},
    "deepseek-v3": {"context_window": 128000},
    "deepseek-reasoner": {"context_window": 128000},
    "gemini-3-pro-preview": {"context_window": 2000000},
    "gemini-3-flash-preview": {"context_window": 1048576},
    "claude-opus-45": {"context_window": 203000},
    "claude-sonnet-45": {"context_window": 200000},
    "zai-org-glm-4.6": {"context_window": 203000},
    "mistral-31-24b": {"context_window": 131000},
    "google-gemma-3-27b-it": {"context_window": 203000},
    "openai-gpt-oss-120b": {"context_window": 131000},
    "kimi-k2-thinking": {"context_window": 262000},
    "deepseek-v3.2": {"context_window": 164000},
    "openai-gpt-52": {"context_window": 262000},
    "venice-uncensored": {"context_window": 33000},
    "cybertron-8b-v3": {"context_window": 8000},
    "dolphin-2.9.4-llama-3-8b": {"context_window": 8000},
    "nous-hermes-2-pro-llama-3-8b": {"context_window": 8000},
    "phi-3-mini-4k-instruct": {"context_window": 4000},
    "v-embed-v1": {"context_window": 8000},
    "v-embed-v2": {"context_window": 16000},
    "text-embedding-3-small": {"context_window": 8000},
    "text-embedding-3-large": {"context_window": 8000},
    "bge-large-en-v1.5": {"context_window": 512},
}

def get_model_context_window(model_id: str) -> int:
    if not isinstance(model_id, str): return 0
    mid = model_id.lower()
    if mid in MODEL_METADATA:
        return MODEL_METADATA[mid]["context_window"]
    norm_mid = mid.split('/')[-1].replace('.', '').replace('-', '')
    best_match = None
    best_len = 0
    for pattern, meta in MODEL_METADATA.items():
        p_clean = pattern.lower().replace('.', '').replace('-', '')
        if p_clean in norm_mid or pattern.lower() in mid:
            if len(pattern) > best_len:
                best_len = len(pattern)
                best_match = meta["context_window"]
    if best_match: return best_match
    if "grok-4.1-fast" in mid or "grok41fast" in mid: return 2000000
    if "gemini-1.5" in mid: return 1048576
    if "gemini-2" in mid: return 1048576
    if "gemini-3" in mid: return 1048576
    if "claude-4.5" in mid: return 1000000
    if "claude-4.6" in mid: return 1000000
    if "claude-3-5" in mid: return 200000
    if "claude-3-opus" in mid: return 200000
    if "claude-3-haiku" in mid: return 200000
    if "claude-3" in mid: return 200000
    if "gpt-4" in mid: return 128000
    if "llama-3.1" in mid: return 128000
    if "llama-3.2" in mid: return 128000
    if "llama-3.3" in mid: return 128000
    if "deepseek" in mid: return 128000
    if "qwen2.5" in mid: return 128000
    return 128000

def resolve_largest_context_model(provider_id: str) -> str:
    from python.helpers.providers import get_provider_config
    p_cfg = get_provider_config("chat", provider_id)
    if not p_cfg: return ""
    models_meta = p_cfg.get("models", [])
    if not models_meta: return ""
    best_model = None
    max_context = -1
    for m in models_meta:
        m_id = m.get("id")
        if not m_id: continue
        context = get_model_context_window(m_id)
        if context > max_context:
            max_context = context
            best_model = m_id
    return best_model or ""

def get_safe_max_tokens(provided_value: int, model_name: str) -> int:
    if provided_value > 0:
        return provided_value
    name_lower = model_name.lower()
    # Issue #1082: Increased from 16384 to 32768 to reduce truncation during code gen
    if any(m in name_lower for m in ["gemini", "grok", "claude", "llama-3.1", "llama-3.2", "llama-3.3"]):
        return 32768
    return 8192