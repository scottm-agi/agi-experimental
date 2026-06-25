"""
EnvIntegrity — Bulletproof configuration synchronization.

Ensures secrets, API keys, parameters, and settings stay in sync across
ALL stores (os.environ, .env file, Secrets DB, MCP subprocess env).

Key properties:
- MD5 hash-based drift detection across stores
- Self-healing: auto-repairs when drift is detected
- Most-recent-wins: timestamp tracks last write, newest value is authoritative
- Loop guard: MD5 check prevents infinite repair cycles
- No container rebuild required — all fixes work at runtime

Usage:
    from python.helpers.env_integrity import EnvIntegrity
    
    # On startup (after dotenv.load_dotenv):
    EnvIntegrity.startup_sync()
    
    # After settings save:
    EnvIntegrity.repair(authoritative={"PERPLEXITY_API_KEY": "new-key"})
    
    # Periodic self-heal:
    healed = EnvIntegrity.check_and_heal()
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("agix.env_integrity")

# ============================================================================
# KEY ALIASES REGISTRY
# Maps canonical provider name → all env var variants that must stay in sync
# ============================================================================
KEY_ALIASES: Dict[str, List[str]] = {
    # LLM Providers
    "OPENROUTER": ["OPENROUTER_API_KEY", "API_KEY_OPENROUTER"],
    "OPENAI": ["OPENAI_API_KEY", "API_KEY_OPENAI"],
    "ANTHROPIC": ["ANTHROPIC_API_KEY", "API_KEY_ANTHROPIC"],
    "GOOGLE": ["GOOGLE_API_KEY", "API_KEY_GOOGLE"],
    "GROQ": ["GROQ_API_KEY", "API_KEY_GROQ"],
    "MISTRAL": ["MISTRAL_API_KEY", "API_KEY_MISTRAL"],
    "DEEPSEEK": ["DEEPSEEK_API_KEY", "API_KEY_DEEPSEEK"],
    "XAI": ["XAI_API_KEY", "API_KEY_XAI"],
    "VENICE": ["VENICE_API_KEY", "API_KEY_VENICE"],
    # Tool/Search Providers
    "PERPLEXITY": ["PERPLEXITY_API_KEY", "API_KEY_PERPLEXITY", "PERPLEXITY"],
    "CONTEXT7": ["CONTEXT7_API_KEY", "API_KEY_CONTEXT7"],
    "TAVILY": ["TAVILY_API_KEY", "API_KEY_TAVILY"],
    # Infrastructure
    "FORGEJO": ["FORGEJO_TOKEN", "API_KEY_FORGEJO"],
    "GITHUB": ["GITHUB_TOKEN", "API_KEY_GITHUB"],
}

# Flatten for quick lookup: env_var_name → canonical provider
_ALIAS_TO_PROVIDER: Dict[str, str] = {}
for _provider, _aliases in KEY_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_PROVIDER[_alias] = _provider

# Track last write timestamps per key for most-recent-wins resolution
_last_write_ts: Dict[str, float] = {}

# MD5 of last known-good state to prevent repair loops
_last_repair_hash: Optional[str] = None


def _md5(value: str) -> str:
    """Compute MD5 hash of a string (delegates to centralized helper)."""
    from python.helpers.hashing import content_hash
    return content_hash(value)


class EnvIntegrity:
    """Central integrity engine for env/secret/parameter synchronization."""

    # ================================================================
    # HASH — MD5-based drift detection
    # ================================================================

    @staticmethod
    def hash_key(key: str) -> Dict[str, Dict[str, Optional[str]]]:
        """Get MD5 hash of a key's value from os.environ.
        
        Returns dict with store names as keys and {value, hash} as values.
        """
        val = os.environ.get(key)
        return {
            "os_environ": {
                "value": val,
                "hash": _md5(val) if val else None,
            }
        }

    @staticmethod
    def hash_all_aliases(provider: str) -> Dict[str, Optional[str]]:
        """Hash all aliases for a provider. Returns alias → MD5 hash."""
        aliases = KEY_ALIASES.get(provider, [])
        result: Dict[str, Optional[str]] = {}
        for alias in aliases:
            val = os.environ.get(alias)
            result[alias] = _md5(val) if val else None
        return result

    # ================================================================
    # AUDIT — Drift detection
    # ================================================================

    @staticmethod
    def audit() -> Dict[str, Dict[str, Any]]:
        """Audit all registered keys for drift across aliases.
        
        Returns a report for each provider:
        {
            "PERPLEXITY": {
                "drifted": True/False,
                "aliases": {"PERPLEXITY_API_KEY": "hash1", ...},
                "values": {"PERPLEXITY_API_KEY": "val1", ...},
            }
        }
        """
        report: Dict[str, Dict[str, Any]] = {}
        
        for provider, aliases in KEY_ALIASES.items():
            values: Dict[str, Optional[str]] = {}
            hashes: Dict[str, Optional[str]] = {}
            
            for alias in aliases:
                val = os.environ.get(alias)
                values[alias] = val
                hashes[alias] = _md5(val) if val else None
            
            # Check if any non-None hashes disagree
            non_none_hashes = [h for h in hashes.values() if h is not None]
            if not non_none_hashes:
                continue  # No values at all, skip
            
            drifted = len(set(non_none_hashes)) > 1
            
            report[provider] = {
                "drifted": drifted,
                "aliases": hashes,
                "values": values,
            }
        
        return report

    @staticmethod
    def full_audit() -> Dict[str, Any]:
        """Cross-check ALL state stores for comprehensive drift detection.

        Checks: os.environ, .env file, Secrets DB, settings.json, parameters.
        Returns a complete report showing per-key state across every store,
        plus a summary of any discrepancies found.

        Useful for debugging provisioned tenant config issues — call via
        the Settings API or a diagnostic tool to see exactly which stores
        are out of sync and why.
        """
        report: Dict[str, Any] = {
            "alias_drift": {},      # KEY_ALIASES alias consistency
            "secrets_drift": [],    # env↔secrets DB mismatches
            "settings_drift": [],   # env↔settings.json mismatches
            "stores": {},           # per-key values across all stores
            "summary": {"total_keys_checked": 0, "drifted": 0, "in_sync": 0},
        }

        # ── 1. Standard alias drift (existing audit) ────────────────
        report["alias_drift"] = EnvIntegrity.audit()

        # ── 2. Secrets DB vs os.environ ──────────────────────────────
        try:
            from python.helpers.secrets_helper import get_default_secrets_manager
            sm = get_default_secrets_manager()
            db_secrets = sm.load_secrets()

            for secret_key in EnvIntegrity._RAILWAY_SECRET_KEYS:
                env_val = os.environ.get(secret_key, "")
                db_val = db_secrets.get(secret_key, "")
                env_hash = _md5(env_val) if env_val else None
                db_hash = _md5(db_val) if db_val else None

                entry = {
                    "key": secret_key,
                    "os_environ": {"set": bool(env_val), "hash": env_hash},
                    "secrets_db": {"set": bool(db_val), "hash": db_hash},
                    "in_sync": env_hash == db_hash,
                }
                report["stores"][secret_key] = entry
                report["summary"]["total_keys_checked"] += 1

                if env_hash != db_hash:
                    report["secrets_drift"].append(entry)
                    report["summary"]["drifted"] += 1
                else:
                    report["summary"]["in_sync"] += 1
        except Exception as e:
            report["secrets_drift"].append({"error": str(e)})

        # ── 3. Settings.json vs os.environ for Railway keys ──────────
        try:
            from python.helpers.settings import get_settings
            current = get_settings()

            for env_key, settings_key in EnvIntegrity._RAILWAY_ENV_TO_SETTINGS.items():
                env_val = os.environ.get(env_key, "")
                settings_val = str(current.get(settings_key, ""))
                env_hash = _md5(env_val) if env_val else None
                settings_hash = _md5(settings_val) if settings_val else None

                entry = {
                    "env_key": env_key,
                    "settings_key": settings_key,
                    "os_environ": {"value": env_val[:50] if env_val else None, "hash": env_hash},
                    "settings_json": {"value": settings_val[:50] if settings_val else None, "hash": settings_hash},
                    "in_sync": env_hash == settings_hash,
                }
                report["stores"][env_key] = entry
                report["summary"]["total_keys_checked"] += 1

                if env_hash != settings_hash:
                    report["settings_drift"].append(entry)
                    report["summary"]["drifted"] += 1
                else:
                    report["summary"]["in_sync"] += 1
        except Exception as e:
            report["settings_drift"].append({"error": str(e)})

        # ── 4. .env file vs os.environ for all aliases ───────────────
        dotenv_raw = EnvIntegrity._read_dotenv_raw()
        dotenv_drift: List[Dict[str, Any]] = []
        for provider, aliases in KEY_ALIASES.items():
            for alias in aliases:
                env_val = os.environ.get(alias, "")
                dotenv_val = dotenv_raw.get(alias, "")
                if env_val and dotenv_val and _md5(env_val) != _md5(dotenv_val):
                    dotenv_drift.append({
                        "key": alias,
                        "provider": provider,
                        "os_environ_hash": _md5(env_val),
                        "dotenv_hash": _md5(dotenv_val),
                    })
        report["dotenv_drift"] = dotenv_drift

        return report

    # ================================================================
    # REPAIR — Self-healing sync
    # ================================================================

    @staticmethod
    def _resolve_authoritative_value(
        provider: str,
        aliases: List[str],
        authoritative: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """Determine the authoritative value for a provider.
        
        Priority:
        1. Explicit authoritative override (from UI save or agent)
        2. Most-recently-written value (timestamp-based)
        3. Majority vote among aliases (fallback)
        4. Any non-empty value
        """
        # 1. Check explicit authoritative override
        if authoritative:
            for alias in aliases:
                if alias in authoritative:
                    val = authoritative[alias]
                    if val and not val.startswith("******"):
                        return val
        
        # 2. Check timestamps — most recent write wins
        best_ts = 0.0
        best_val = None
        for alias in aliases:
            ts = _last_write_ts.get(alias, 0.0)
            val = os.environ.get(alias)
            if val and ts > best_ts:
                best_ts = ts
                best_val = val
        
        if best_val and best_ts > 0:
            return best_val
        
        # 3. Majority vote — most common non-empty value wins
        values = [os.environ.get(a) for a in aliases if os.environ.get(a)]
        if values:
            counter = Counter(values)
            return counter.most_common(1)[0][0]
        
        return None

    @staticmethod
    def _write_to_dotenv(updates: Dict[str, str]) -> None:
        """Persist key-value pairs to .env file."""
        try:
            from python.helpers import dotenv_manager as dotenv
            dotenv.save_dotenv_values(updates)
        except Exception as e:
            logger.warning(f"[env_integrity] Failed to write to .env: {e}")

    @staticmethod
    def _write_to_secrets_db(updates: Dict[str, str]) -> None:
        """Persist key-value pairs to Secrets DB."""
        try:
            from python.helpers.secrets_helper import get_default_secrets_manager
            manager = get_default_secrets_manager()
            for key, value in updates.items():
                if value:
                    # Use config_db directly to avoid recursion through SecretsManager
                    from python.helpers import config_db
                    config_db.set_secret(key, value, "global")
            manager.clear_cache()
        except Exception as e:
            logger.warning(f"[env_integrity] Failed to write to secrets DB: {e}")

    @staticmethod
    def _read_dotenv_raw() -> Dict[str, str]:
        """Read all key-value pairs from .env file directly (bypass override=False)."""
        result: Dict[str, str] = {}
        try:
            from python.helpers.files import get_abs_path
            dotenv_path = get_abs_path(".env")
            if not os.path.isfile(dotenv_path):
                return result
            with open(dotenv_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)', line)
                    if match:
                        key = match.group(1)
                        value = match.group(2).strip()
                        # Strip surrounding quotes
                        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                            value = value[1:-1]
                        if value:
                            result[key] = value
        except Exception as e:
            logger.warning(f"[env_integrity] Failed to read .env raw: {e}")
        return result

    @staticmethod
    def repair(authoritative: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Detect drift and repair ALL stores to match.
        
        Args:
            authoritative: Optional dict of key→value overrides (from UI save).
                          These take absolute priority.
        
        Returns:
            Dict of provider → winning value that was synced.
            
        Loop guard: MD5 of combined state is checked. If it matches the
        last repair, we skip to prevent infinite loops.
        """
        global _last_repair_hash
        
        # Compute current state hash for loop guard
        state_parts = []
        for provider, aliases in KEY_ALIASES.items():
            for alias in aliases:
                val = os.environ.get(alias, "")
                state_parts.append(f"{alias}={val}")
        current_hash = _md5("|".join(sorted(state_parts)))
        
        # If we have authoritative overrides, always proceed
        # Otherwise, check loop guard
        if not authoritative and current_hash == _last_repair_hash:
            logger.debug("[env_integrity] repair() skipped — MD5 matches last repair (loop guard)")
            return {}
        
        synced: Dict[str, str] = {}
        all_dotenv_updates: Dict[str, str] = {}
        all_db_updates: Dict[str, str] = {}
        
        for provider, aliases in KEY_ALIASES.items():
            winning_value = EnvIntegrity._resolve_authoritative_value(
                provider, aliases, authoritative
            )
            
            if not winning_value:
                continue
            
            # Check if any alias disagrees
            needs_sync = False
            for alias in aliases:
                current = os.environ.get(alias)
                if current != winning_value:
                    needs_sync = True
                    break
            
            if not needs_sync:
                continue
            
            # Sync ALL aliases to winning value
            now = time.time()
            for alias in aliases:
                os.environ[alias] = winning_value
                _last_write_ts[alias] = now
                all_dotenv_updates[alias] = winning_value
                all_db_updates[alias] = winning_value
            
            synced[provider] = winning_value
            logger.info(
                f"[env_integrity] REPAIRED {provider}: "
                f"synced {len(aliases)} aliases to value ending ...{winning_value[-8:] if len(winning_value) > 8 else '***'}"
            )
        
        # Batch-write to persistent stores
        if all_dotenv_updates:
            EnvIntegrity._write_to_dotenv(all_dotenv_updates)
        if all_db_updates:
            EnvIntegrity._write_to_secrets_db(all_db_updates)
        
        # Update loop guard hash AFTER repair
        new_state_parts = []
        for provider, aliases in KEY_ALIASES.items():
            for alias in aliases:
                val = os.environ.get(alias, "")
                new_state_parts.append(f"{alias}={val}")
        _last_repair_hash = _md5("|".join(sorted(new_state_parts)))
        
        if synced:
            logger.info(f"[env_integrity] Repair complete: {len(synced)} providers synced")
        
        return synced

    # ================================================================
    # STARTUP SYNC — .env overrides stale container env
    # ================================================================

    @staticmethod
    def startup_sync() -> Dict[str, str]:
        """Called at process startup to force .env values into os.environ.
        
        Railway/Docker sets env vars at container boot which can be stale.
        The .env file represents the user's LAST SAVED INTENT, so it takes
        priority for API keys.
        
        Does NOT override auth passwords (Railway-managed credentials).
        
        Returns dict of keys that were overridden.
        """
        overridden: Dict[str, str] = {}
        
        # Read .env file directly (bypass python-dotenv override=False)
        dotenv_values = EnvIntegrity._read_dotenv_raw()
        
        if not dotenv_values:
            logger.debug("[env_integrity] startup_sync: no .env values found")
            return overridden
        
        # Only override API keys/tokens, NOT auth passwords
        # Auth passwords from Railway should take priority
        auth_keys = {"AUTH_LOGIN", "AUTH_PASSWORD", "RFC_PASSWORD", "ROOT_PASSWORD"}
        
        now = time.time()
        for key, dotenv_val in dotenv_values.items():
            if key in auth_keys:
                continue  # Don't override Railway auth credentials
            
            # Only override for known sensitive patterns
            key_up = key.upper()
            is_api_key = any(p in key_up for p in [
                "API_KEY", "_TOKEN", "PERPLEXITY", "CONTEXT7",
                "OPENROUTER", "ANTHROPIC", "OPENAI", "GOOGLE",
                "GROQ", "MISTRAL", "DEEPSEEK", "XAI", "VENICE",
                "TAVILY", "FORGEJO", "GITHUB",
            ])
            
            if not is_api_key:
                continue
            
            current = os.environ.get(key)
            if current != dotenv_val:
                os.environ[key] = dotenv_val
                _last_write_ts[key] = now
                overridden[key] = dotenv_val
                logger.info(
                    f"[env_integrity] startup_sync: {key} overridden "
                    f"(was {'set' if current else 'unset'} → .env value)"
                )
        
        # After overriding individual keys, run a repair pass to sync aliases
        if overridden:
            EnvIntegrity.repair()
            
            # Sync NON-API-KEY secrets to DB (tokens, webhook secrets, etc.).
            # API keys belong in os.environ (already set above), not secrets DB.
            # They're managed via .env / Settings > API Keys and accessed via
            # get_api_key() / os.environ — writing them to secrets DB causes
            # contamination in the Secrets UI.
            from python.helpers.secrets_helper import is_system_api_key
            non_api_key_overrides = {
                k: v for k, v in overridden.items()
                if not is_system_api_key(k)
            }
            if non_api_key_overrides:
                EnvIntegrity._write_to_secrets_db(non_api_key_overrides)
            
            logger.info(
                f"[env_integrity] startup_sync complete: "
                f"{len(overridden)} keys overridden from .env"
            )
        
        return overridden

    # ================================================================
    # SELF-HEAL — Detect + repair in one call
    # ================================================================

    @staticmethod
    def check_and_heal() -> List[str]:
        """Auto-detect drift and self-heal. Returns list of healed provider names.
        
        Safe to call frequently — MD5 loop guard prevents unnecessary work.
        """
        report = EnvIntegrity.audit()
        drifted_providers = [p for p, info in report.items() if info.get("drifted")]
        
        if not drifted_providers:
            return []
        
        logger.warning(
            f"[env_integrity] DRIFT DETECTED in {len(drifted_providers)} providers: "
            f"{', '.join(drifted_providers)}"
        )
        
        synced = EnvIntegrity.repair()
        healed = list(synced.keys())
        
        if healed:
            logger.info(f"[env_integrity] Self-healed: {', '.join(healed)}")
        
        return healed

    # ================================================================
    # RECORD WRITE — Track timestamps for most-recent-wins
    # ================================================================

    @staticmethod
    def record_write(key: str, value: str) -> None:
        """Record that a key was written (for most-recent-wins resolution).
        
        Call this after any intentional key update (UI save, agent set_secret).
        """
        _last_write_ts[key] = time.time()
        
        # Also update os.environ immediately
        os.environ[key] = value
        
        # If this key is a known alias, sync ALL aliases for the provider
        provider = _ALIAS_TO_PROVIDER.get(key)
        if provider:
            aliases = KEY_ALIASES[provider]
            now = time.time()
            for alias in aliases:
                os.environ[alias] = value
                _last_write_ts[alias] = now

    # ================================================================
    # NOTE: sync_parameters_to_env() was removed here (System 1 consolidation,
    # ITR-44). It had 0 callers — parameters are consumed via parameter_get()
    # and merged into current_settings during initialize_agent(). There is no
    # use case for PARAM_* env vars.
    # ================================================================

    # ================================================================
    # RAILWAY ENV → STORES — Reverse sync for provisioned tenants
    # ================================================================

    # Railway env vars that map to settings.json keys (non-secret config).
    # Format: env var name → settings.json key
    _RAILWAY_ENV_TO_SETTINGS: Dict[str, str] = {
        "EVENT_HOOKS_REPOS": "event_hooks_repos",
        "EVENT_HOOKS_ENABLED": "event_hooks_enabled",
        "AGIX_TASKS_ENABLED": "tasks_enabled",
    }

    # Railway env vars that must be in Secrets DB (already in KEY_ALIASES
    # for alias drift, but need explicit env→DB write on boot for
    # provisioned tenants where Railway env is authoritative, not .env).
    _RAILWAY_SECRET_KEYS: List[str] = [
        "GITHUB_TOKEN",
        "GITHUB_WEBHOOK_SECRET",
        "GITHUB_PAT",
        "FORGEJO_TOKEN",
        "FORGEJO_WEBHOOK_SECRET",
    ]

    @staticmethod
    def sync_railway_env_to_stores() -> Dict[str, Any]:
        """Reconcile Railway env vars into settings.json and Secrets DB.

        Called once on boot for Railway-provisioned tenants. Unlike
        startup_sync() which treats .env as authoritative, this method
        treats os.environ (set by Railway on container rebuild) as the
        source of truth for GITHUB_TOKEN, EVENT_HOOKS_REPOS, etc.

        Idempotent: only writes when the stored value differs from
        the env var. Uses MD5 comparison to avoid unnecessary disk I/O.

        Returns summary dict:
            {"settings_updated": [...], "secrets_updated": [...], "skipped": [...]}
        """
        result: Dict[str, Any] = {
            "settings_updated": [],
            "secrets_updated": [],
            "skipped": [],
        }

        # ── 1. Railway env → settings.json ───────────────────────────
        try:
            from python.helpers.settings import get_settings, set_settings_delta

            current = get_settings()
            delta: Dict[str, Any] = {}

            for env_key, settings_key in EnvIntegrity._RAILWAY_ENV_TO_SETTINGS.items():
                env_val = os.environ.get(env_key)
                if env_val is None:
                    result["skipped"].append(env_key)
                    continue

                # Type coercion for booleans
                if settings_key in ("event_hooks_enabled", "tasks_enabled"):
                    typed_val: Any = env_val.lower() in ("true", "1", "yes")
                else:
                    typed_val = env_val

                current_val = current.get(settings_key)

                # MD5 comparison to avoid unnecessary writes
                if _md5(str(current_val)) != _md5(str(typed_val)):
                    delta[settings_key] = typed_val
                    result["settings_updated"].append(settings_key)
                    logger.info(
                        f"[env_integrity] railway_sync: settings.{settings_key} updated from env"
                    )

            if delta:
                set_settings_delta(delta, apply=False)
                logger.info(
                    f"[env_integrity] railway_sync: persisted "
                    f"{len(delta)} setting(s) to data/settings.json"
                )

        except Exception as e:
            logger.error(f"[env_integrity] railway_sync settings failed: {e}")

        # ── 2. Railway env → Secrets DB ──────────────────────────────
        try:
            from python.helpers.secrets_helper import get_default_secrets_manager
            sm = get_default_secrets_manager()
            existing = sm.load_secrets()

            db_updates: Dict[str, str] = {}
            for secret_key in EnvIntegrity._RAILWAY_SECRET_KEYS:
                env_val = os.environ.get(secret_key)
                if not env_val:
                    result["skipped"].append(secret_key)
                    continue

                existing_val = existing.get(secret_key, "")
                if _md5(env_val) != _md5(existing_val):
                    db_updates[secret_key] = env_val
                    result["secrets_updated"].append(secret_key)
                    logger.info(
                        f"[env_integrity] railway_sync: secret {secret_key} updated "
                        f"(len {len(existing_val)} → {len(env_val)})"
                    )

            if db_updates:
                EnvIntegrity._write_to_secrets_db(db_updates)
                # Also record writes for alias sync
                now = time.time()
                for k, v in db_updates.items():
                    _last_write_ts[k] = now

            # Force secrets → os.environ for immediate availability
            sm.sync_to_environ(force=True)

        except Exception as e:
            logger.error(f"[env_integrity] railway_sync secrets failed: {e}")

        # ── Summary ──────────────────────────────────────────────────
        n_s = len(result["settings_updated"])
        n_sec = len(result["secrets_updated"])
        if n_s or n_sec:
            logger.info(
                f"[env_integrity] railway_sync complete: "
                f"{n_s} setting(s), {n_sec} secret(s) reconciled"
            )
        else:
            logger.debug("[env_integrity] railway_sync: all stores in sync")

        return result

