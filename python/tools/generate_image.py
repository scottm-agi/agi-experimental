from __future__ import annotations
import asyncio
import time
import os
import io
import logging
import base64
import httpx
from typing import Any
from python.helpers.tool import Tool, Response
from python.helpers.print_style import PrintStyle
from python.helpers import files, settings
from python.helpers.model_wrappers.utils import get_api_key
from python.helpers.retry_strategy import classify_error

logger = logging.getLogger("generate-image")

# Default models per provider
DEFAULT_OPENROUTER_MODEL = "google/gemini-3.1-flash-image-preview"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-image-preview"
DEFAULT_OPENAI_MODEL = "dall-e-3"

# Provider -> default model mapping for fallback
DEFAULT_MODEL_BY_PROVIDER = {
    "openrouter": DEFAULT_OPENROUTER_MODEL,
    "gemini": DEFAULT_GEMINI_MODEL,
    "openai": DEFAULT_OPENAI_MODEL,
    "dall-e": DEFAULT_OPENAI_MODEL,
    "dalle": DEFAULT_OPENAI_MODEL,
}

# Heuristic keywords: all valid image generation models contain one of these
# in their model ID. This is used to catch agent hallucinations like
# "google/gemini-pro-1.5" which is a chat model, not an image model.
_IMAGE_MODEL_KEYWORDS = ("image", "dall-e", "dalle")

# F-13: Fallback models on OpenRouter. When the primary model fails with a
# permanent error (like OpenRouter's garbage 401 "User not found" on
# gemini-3.1-flash-image-preview during ITR-42), try these models in order
# on the SAME provider before switching providers entirely.
OPENROUTER_FALLBACK_MODELS = [
    "google/gemini-3-pro-image-preview",
]

# Full provider fallback chain (model-level fallback first, then providers).
# Each entry is (provider, model). Built dynamically from config + fallback.
PROVIDER_FALLBACK_CHAIN = ["openrouter", "gemini", "openai"]

# Patterns that indicate a PERMANENT provider-level error worth falling back
# from. These are NOT content errors (safety filter) or transient (429/5xx).
_PERMANENT_PROVIDER_PATTERNS = (
    "not installed",           # SDK not available
    "api key is required",     # No API key configured
    "api key",                 # Generic API key issues
    "user not found",          # OpenRouter garbage 401 (ITR-42)
    "account not found",       # Account-level issue
    "does not exist",          # User/account doesn't exist
    "permission denied",       # Access denied
    "invalid api key",         # Bad key
    "authentication",          # Auth failures
    "not a valid model",       # OpenRouter 400: bad model slug
    "no endpoints available",  # OpenRouter 404: data policy/guardrail block
)

# Patterns that indicate a TRANSIENT error (worth retrying)
_TRANSIENT_ERROR_PATTERNS = (
    "429",           # Rate limit
    "500",           # Internal server error
    "502",           # Bad gateway
    "503",           # Service unavailable
    "504",           # Gateway timeout
    "timeout",       # Connection/read timeout
    "timed out",     # Alternate timeout wording
    "connection",    # Connection refused/reset
    "ECONNREFUSED",  # Node-style connection refused
    "ECONNRESET",    # Node-style connection reset
)


def _fetch_openrouter_key_info(api_key: str) -> dict:
    """Fetch OpenRouter API key info (usage, limit) via GET /auth/key.

    RCA-EVAL-1 F-10: During ITR-42, the OpenRouter budget was exceeded
    ($2,342 usage vs $360 limit) but generate_image.py had no way to detect
    this — it made 51 API calls that all returned 401 "User not found"
    (OpenRouter's misleading error for budget exhaustion).

    Returns dict with 'data' key containing 'usage' and 'limit', or raises.
    """
    import urllib.request
    import ssl

    url = "https://openrouter.ai/api/v1/auth/key"
    headers = {"Authorization": f"Bearer {api_key}"}
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        import json as _json
        return _json.loads(resp.read().decode())


def check_openrouter_budget(api_key: str) -> str | None:
    """Check if the OpenRouter API key has exceeded its budget.

    RCA-EVAL-1 F-10: Pre-flight check before image generation calls.
    Returns None if budget is OK, or an error message string if exceeded.
    Fails open (returns None) if the check itself fails — never blocks
    image gen due to a network error in the pre-check.

    FIX (RCA-ITR49): OpenRouter returns limit_remaining which is the ACTUAL
    availability. The old code compared data["usage"] (ALL-TIME cumulative)
    against data["limit"] (periodic cap e.g. weekly), which is WRONG —
    usage accumulates forever while limit resets. Use limit_remaining <= 0
    as the authoritative check.
    """
    try:
        info = _fetch_openrouter_key_info(api_key)
        data = info.get("data", {})
        limit = data.get("limit")

        # Unlimited budget
        if limit is None:
            return None

        # Use limit_remaining — the authoritative field from OpenRouter.
        # This accounts for the limit_reset period (daily/weekly/monthly).
        limit_remaining = data.get("limit_remaining")
        if limit_remaining is not None:
            remaining_f = float(limit_remaining)
            if remaining_f <= 0:
                limit_f = float(limit)
                reset = data.get("limit_reset", "unknown")
                return (
                    f"Error: OpenRouter budget exhausted — "
                    f"${remaining_f:.2f} remaining of ${limit_f:.2f} {reset} limit. "
                    f"Increase the budget limit at https://openrouter.ai/settings/limits "
                    f"or use a different provider."
                )
            return None  # Budget available

        # Fallback: if limit_remaining not in response, fail-open
        return None
    except Exception as e:
        # Fail-open: don't block image gen if the pre-check itself fails
        logger.debug(f"[IMAGE GEN] Budget pre-check failed (non-fatal): {e}")
        return None

def _is_transient_error(error_message: str) -> bool:
    """Determine if an error message indicates a transient (retryable) failure.

    Delegates to retry_strategy.classify_error() for 2-tier auth classification:
    - "transient" (429, 5xx, timeouts) → retryable
    - "transient_auth" (401/403 proxy errors) → retryable with limited ceiling
    - "permanent_auth" (invalid api key, permission denied) → NOT retryable
    - All other categories → NOT retryable

    ISSUE-8: Replaces the old substring-match against _TRANSIENT_ERROR_PATTERNS
    which treated ALL 401s as permanent. Now 401/403 are retryable (proxy providers
    like OpenRouter rotate keys), while truly permanent auth errors are not.
    """
    if not error_message:
        return False
    category = classify_error(error_message)
    return category in ("transient", "transient_auth")


def _is_permanent_provider_error(error_message: str) -> bool:
    """Determine if an error is a permanent provider-level failure worth
    falling back from (F-13).

    Returns True for errors where trying a DIFFERENT model/provider would help:
    - SDK not installed, API key missing, auth failures, "User not found"

    Returns False for errors where fallback won't help:
    - Transient errors (429, 5xx) — retry handles these
    - Safety filter blocks — content-specific, same on any model
    - Empty/success messages
    """
    if not error_message or not error_message.startswith("Error:"):
        return False

    msg_lower = error_message.lower()

    # Safety filter / content blocks — fallback won't help
    if "safety filter" in msg_lower or "blocked" in msg_lower:
        return False

    # Check against permanent provider patterns
    for pattern in _PERMANENT_PROVIDER_PATTERNS:
        if pattern in msg_lower:
            return True

    return False

def resolve_output_path(output_path: str | None, project_dir: str | None) -> str | None:
    """Resolve an output_path for gen_image into a validated absolute path.

    When agents need images at specific project locations (e.g., public/hero.png),
    this eliminates the need for a manual `mv` via code_execution_tool.

    Args:
        output_path: Relative or absolute path specified by the agent.
        project_dir: Active project directory (absolute path), or None.

    Returns:
        Absolute path within the project dir, or None if output_path is empty
        (which signals the caller to use the default tmp behavior).

    Raises:
        ValueError: If the path resolves outside the project sandbox.
    """
    if not output_path:
        return None

    if not project_dir:
        raise ValueError(
            "Cannot use output_path without an active project. "
            "No active project directory found."
        )

    if os.path.isabs(output_path):
        resolved = os.path.normpath(output_path)
    else:
        resolved = os.path.normpath(os.path.join(project_dir, output_path))

    project_dir_norm = os.path.normpath(project_dir)
    if not resolved.startswith(project_dir_norm + os.sep) and resolved != project_dir_norm:
        raise ValueError(
            f"output_path resolves OUTSIDE active project sandbox: "
            f"'{output_path}' → '{resolved}' is not within '{project_dir_norm}/'"
        )

    parent = os.path.dirname(resolved)
    os.makedirs(parent, exist_ok=True)

    return resolved


class GenerateImage(Tool):
    """
    Generates high-quality images from text descriptions using AI.
    Supports multiple providers:
      - "openrouter" (default): Gemini Nano Banana via OpenRouter (OpenAI-compatible)
      - "gemini": Google Nano Banana via google-genai SDK (direct)
      - "openai": DALL-E via LiteLLM

    Configuration via Settings UI (Models tab > Image Generation):
      - image_gen_provider: "openrouter" | "gemini" | "openai"
      - image_gen_model: Model ID (provider-specific)

    Image Context Chain (reference_image):
      When generating a sequence of related images (e.g., multi-screen mockups),
      pass `reference_image` pointing to a previously generated image. The tool
      will include it as visual context in the API call, ensuring consistent
      styling, colors, typography, and layout across all generated images.

      Pipeline: Theme Panel → First Mockup (ref=theme) → N+ Screens (ref=first)

    API Keys (resolved via get_api_key() utility, same as LLM chat):
      - API_KEY_OPENROUTER or OPENROUTER_API_KEY: For openrouter provider
      - API_KEY_GEMINI or GEMINI_API_KEY: For gemini provider
      - Also checks dotenv fallbacks automatically

    Environment variable overrides (for Docker / CI):
      - IMAGE_GEN_PROVIDER: overrides settings provider
      - IMAGE_GEN_MODEL: overrides settings model
    """

    def _get_config(self) -> tuple[str, str]:
        """Get image gen provider and model from settings, with env var override.
        
        HARD ENFORCEMENT: Validates the model name contains image-generation
        keywords (e.g. 'image', 'dall-e'). If the model doesn't look like an
        image generation model (agent hallucination via settings_set), falls
        back to the provider's default model.
        """
        try:
            s = settings.get_settings()
            provider = s.get("image_gen_provider", "openrouter")
            model = s.get("image_gen_model", DEFAULT_OPENROUTER_MODEL)
        except Exception:
            provider = "openrouter"
            model = DEFAULT_OPENROUTER_MODEL

        # Env vars override settings (for Docker/CI deployments)
        provider = os.environ.get("IMAGE_GEN_PROVIDER", provider).lower()
        model_override = os.environ.get("IMAGE_GEN_MODEL")
        if model_override:
            model = model_override

        # HARD VALIDATION: Reject models that don't look like image generation models.
        # All valid image gen models contain 'image' or 'dall-e' in their name.
        # This catches agent hallucinations like 'google/gemini-pro-1.5'.
        model_lower = model.lower()
        if not any(kw in model_lower for kw in _IMAGE_MODEL_KEYWORDS):
            default_model = DEFAULT_MODEL_BY_PROVIDER.get(provider, DEFAULT_OPENROUTER_MODEL)
            logger.warning(
                f"Image gen model '{model}' does not appear to be an image generation model "
                f"(expected model name to contain one of: {_IMAGE_MODEL_KEYWORDS}). "
                f"Falling back to provider default: '{default_model}'. "
                f"This usually means an agent hallucinated a model name via settings_set."
            )
            model = default_model

        return provider, model

    # -------------------------------------------------------------------
    # Reference image resolution for context chain
    # -------------------------------------------------------------------
    def _resolve_reference_image(self) -> str | None:
        """Resolve the reference_image arg to an absolute file path.

        Returns None if no reference_image was provided.
        Raises ValueError if the path doesn't exist.
        """
        ref = self.args.get("reference_image", "")
        if not ref:
            return None

        if os.path.isabs(ref):
            if not os.path.isfile(ref):
                raise ValueError(
                    f"reference_image does not exist: '{ref}'"
                )
            return ref

        # Relative path — resolve against project dir
        project_dir = self._get_project_dir()
        if project_dir:
            resolved = os.path.normpath(os.path.join(project_dir, ref))
            if os.path.isfile(resolved):
                return resolved

        raise ValueError(
            f"reference_image does not exist: '{ref}' "
            f"(tried absolute and relative to project dir)"
        )

    def _get_project_dir(self) -> str | None:
        """Get the active project directory path."""
        try:
            from python.helpers import projects
            project_name = projects.get_context_project_name(self.agent.context)
            if project_name:
                return projects.get_project_folder(project_name)
        except Exception:
            pass
        return None

    def _build_reference_content(self, ref_path: str) -> dict:
        """Build a content dict for the reference image (base64 encoded).

        Returns a dict like: {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
        suitable for OpenRouter/OpenAI message content arrays.
        Also includes a 'data' key with raw base64 for Gemini's inline_data.
        """
        with open(ref_path, "rb") as f:
            img_bytes = f.read()

        b64_data = base64.b64encode(img_bytes).decode("utf-8")

        # Detect MIME type from extension
        ext = os.path.splitext(ref_path)[1].lower()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
        mime_type = mime_map.get(ext, "image/png")

        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{b64_data}"},
            "data": b64_data,
            "mime_type": mime_type,
        }

    async def execute(self, prompt: str = "", **kwargs: Any) -> Response:
        await self.agent.handle_intervention()

        # ── RCA-454: Hard cap on image generation per agent session ──────────
        # Each generate_image call has different args (different mockup names),
        # so same-message dedup never fires. The supervisor redirected 20 times
        # but the agent ignored the advisory nudges. This is a HARD BLOCK.
        from python.helpers.gate_config import MAX_IMAGE_GENERATIONS_PER_SESSION
        img_count = self.agent.data.get("_generate_image_count", 0)
        if img_count >= MAX_IMAGE_GENERATIONS_PER_SESSION:
            logger.warning(
                f"[generate_image] HARD CAP: {img_count}/{MAX_IMAGE_GENERATIONS_PER_SESSION} "
                f"images generated this session. Blocking further calls."
            )
            return Response(
                message=(
                    f"🛑 **Image generation budget exhausted** "
                    f"({img_count}/{MAX_IMAGE_GENERATIONS_PER_SESSION} images generated).\n\n"
                    f"You have created enough design mockups for this phase. "
                    f"Use `response` to submit your completed work and proceed "
                    f"to the next phase. Do NOT call generate_image again."
                ),
                break_loop=False,
            )
        # Increment counter BEFORE the call (so failures also count against budget)
        self.agent.data["_generate_image_count"] = img_count + 1

        prompt = prompt or self.args.get("prompt", "")
        if not prompt:
            return Response(
                message="Error: Missing 'prompt' argument for image generation.",
                break_loop=False,
            )

        # Broad `instruction` parameter: calling agents can inject domain-specific
        # rules (e.g., frontend STYLE_PREFIX for browser-screenshot mockups,
        # art direction for illustrations, brand guidelines, etc.).
        # When provided, instruction is prepended to the prompt with a separator.
        instruction = self.args.get("instruction", "")
        if instruction:
            prompt = f"{instruction}\n---\n{prompt}"

        # FIX-4: Optional output_path for direct project-scoped saves
        self._output_path = self.args.get("output_path", "")

        # Image Context Chain: resolve reference_image for style consistency
        reference_image_path = None
        try:
            reference_image_path = self._resolve_reference_image()
        except ValueError as e:
            logger.warning(f"reference_image resolution failed (continuing without): {e}")

        if reference_image_path:
            PrintStyle(font_color="#CC34C3", bold=True, padding=True).print(
                f"Generating image with reference context: '{os.path.basename(reference_image_path)}'"
            )
            PrintStyle(font_color="#CC34C3", padding=True).print(
                f"Prompt: '{prompt[:200]}...'"
            )
        else:
            PrintStyle(font_color="#CC34C3", bold=True, padding=True).print(
                f"Generating image for prompt: '{prompt[:200]}...'"
            )

        provider, model = self._get_config()

        if self._output_path:
            PrintStyle(font_color="#CC34C3", padding=True).print(
                f"Provider: {provider} | Model: {model} | Output: {self._output_path}"
            )
        else:
            PrintStyle(font_color="#CC34C3", padding=True).print(
                f"Provider: {provider} | Model: {model}"
            )

        # F-13: Model + provider fallback chain.
        # 1. Try primary model on configured provider (e.g. openrouter/gemini-flash-image)
        # 2. If permanent error → try fallback model on SAME provider (openai/gpt-5.4-image-2)
        # 3. If that also fails → try other providers (gemini direct, openai direct)
        from python.helpers.rate_limiter import RetryWithBackoff, RateLimitExceededError

        # Build the fallback sequence: [(provider, model), ...]
        fallback_sequence = [(provider, model)]

        # Add OpenRouter fallback models if primary is openrouter
        if provider == "openrouter":
            for fb_model in OPENROUTER_FALLBACK_MODELS:
                if fb_model != model:
                    fallback_sequence.append(("openrouter", fb_model))

        # SS-10: Add cross-provider fallback from PROVIDER_FALLBACK_CHAIN.
        # When the primary provider fails permanently (budget exceeded, invalid key),
        # try other providers rather than giving up entirely.
        for alt_provider in PROVIDER_FALLBACK_CHAIN:
            if alt_provider != provider:
                alt_model = DEFAULT_MODEL_BY_PROVIDER.get(alt_provider)
                if alt_model:
                    fallback_sequence.append((alt_provider, alt_model))

        last_error = None
        tried = []

        for fb_provider, fb_model in fallback_sequence:
            tried.append(f"{fb_provider}/{fb_model}")

            if len(tried) > 1:
                PrintStyle(font_color="#CC34C3", padding=True).print(
                    f"[F-13 FALLBACK] Trying {fb_provider}/{fb_model}..."
                )

            retry = RetryWithBackoff(max_retries=3, initial_delay=2.0, max_delay=30.0)

            async def _dispatch_with_retry(p=fb_provider, m=fb_model):
                result = await self._dispatch_provider(p, m, prompt, reference_image_path=reference_image_path)
                if result.message and result.message.startswith("Error:"):
                    if _is_transient_error(result.message):
                        raise RateLimitExceededError(result.message)
                    return result
                return result

            try:
                result = await retry.execute(_dispatch_with_retry)
                if result.message and result.message.startswith("Error:"):
                    if _is_permanent_provider_error(result.message):
                        last_error = result.message
                        logger.warning(
                            f"[IMAGE GEN] F-13: {fb_provider}/{fb_model} failed with "
                            f"permanent error, trying next. Error: {result.message[:200]}"
                        )
                        continue  # Try next in chain
                    return result  # Non-permanent, non-transient → return to agent
                return result  # Success!
            except RateLimitExceededError as e:
                last_error = str(e)
                logger.warning(
                    f"[IMAGE GEN] F-13: {fb_provider}/{fb_model} transient retries "
                    f"exhausted, trying next."
                )
                continue  # Try next provider

        # All providers failed
        logger.error(
            f"[IMAGE GEN] F-13: All providers exhausted. "
                f"Tried: {tried}. Last error: {last_error}"
        )
        return Response(
            message=f"Error: Image generation failed on all providers "
                    f"({', '.join(tried)}). Last error: {last_error}",
            break_loop=True,
        )

    async def _dispatch_provider(self, provider: str, model: str, prompt: str, reference_image_path: str | None = None) -> Response:
        """Dispatch to the unified litellm image generation path.
        
        All providers go through litellm — the SAME infrastructure agents use
        for LLM chat. This ensures key resolution, headers, and base_url are
        identical. No more separate OpenAI/genai client creation.
        """
        return await self._generate_via_litellm(provider, model, prompt, reference_image_path)

    async def _generate_via_litellm(self, provider: str, model: str, prompt: str, reference_image_path: str | None = None) -> Response:
        """Unified image generation via litellm — same path as agent LLM calls.
        
        Uses:
        - get_api_key(provider) — same key resolution as agents
        - _adjust_call_args() from models.py — same headers (X-Title, HTTP-Referer)
        - litellm for the actual API call — same library as agents
        
        Supports: openrouter, gemini, openai providers.
        """
        try:
            import litellm
        except ImportError:
            return Response(
                message="Error: litellm is not installed. Image generation requires litellm.",
                break_loop=False,
            )

        # Use the SAME key resolution as agents (models.py:_get_litellm_chat)
        api_key = get_api_key(provider)
        if not api_key or api_key in ("None", "NA", ""):
            # Try alternate key names (same pattern as agents)
            alt_names = {
                "gemini": "google",
                "openrouter": "openrouter", 
                "openai": "openai",
            }
            alt = alt_names.get(provider)
            if alt and alt != provider:
                api_key = get_api_key(alt)

        if not api_key or api_key in ("None", "NA", ""):
            return Response(
                message=f"Error: No API key found for provider '{provider}'. "
                        f"Set it in Settings > Secrets or as an environment variable.",
                break_loop=False,
            )

        # Build the litellm model string (provider/model format)
        if provider == "openrouter":
            litellm_model = f"openrouter/{model}"
        elif provider == "gemini":
            litellm_model = f"gemini/{model}"
        elif provider in ("openai", "dall-e", "dalle"):
            litellm_model = f"openai/{model}" if "/" not in model else model
        else:
            litellm_model = f"{provider}/{model}"

        # Build kwargs — use _adjust_call_args from models.py for headers
        # This is the SAME function agents use for OpenRouter headers
        from python.models import _adjust_call_args
        kwargs: dict = {}
        _, _, kwargs = _adjust_call_args(provider, model, kwargs)

        # Extract extra_headers from kwargs (litellm uses them differently for image_generation)
        extra_headers = kwargs.pop("extra_headers", {})

        logger.info(
            f"[IMAGE_GEN] Unified path: provider={provider} model={litellm_model} "
            f"key=...{api_key[-4:] if api_key else 'NONE'} headers={list(extra_headers.keys())}"
        )

        try:
            # For OpenRouter: use chat.completions with modalities=["text", "image"]
            # because OpenRouter's image gen goes through the chat endpoint, not
            # the /images/generations endpoint that litellm.image_generation() uses.
            if provider == "openrouter":
                return await self._generate_openrouter_chat(
                    litellm_model, api_key, prompt, extra_headers, reference_image_path
                )

            # For Gemini: use google-genai SDK directly (litellm.image_generation
            # doesn't support Gemini's native image gen with response_modalities)
            if provider == "gemini":
                return await self._generate_gemini_native(
                    model, api_key, prompt, reference_image_path
                )

            # For OpenAI/DALL-E: litellm.image_generation works natively
            size = self.args.get("size", "1024x1024")
            quality = self.args.get("quality", "standard")

            gen_kwargs = {
                "model": litellm_model,
                "api_key": api_key,
                "prompt": prompt,
                "size": size,
                "quality": quality,
                "n": 1,
            }
            if extra_headers:
                gen_kwargs["extra_headers"] = extra_headers

            response = litellm.image_generation(**gen_kwargs)

            image_data = response.data[0]

            gen_id = self.agent.context.generate_id()
            filename = f"gen_image_{gen_id}.png"
            save_path, rel_path = self._get_storage_paths(filename)
            files.make_dirs(save_path)

            if hasattr(image_data, "b64_json") and image_data.b64_json:
                img_bytes = base64.b64decode(image_data.b64_json)
                with open(save_path, "wb") as f:
                    f.write(img_bytes)
            elif hasattr(image_data, "url") and image_data.url:
                async with httpx.AsyncClient(timeout=60) as client:
                    img_response = await client.get(image_data.url)
                    img_response.raise_for_status()
                    with open(save_path, "wb") as f:
                        f.write(img_response.content)
            else:
                return Response(
                    message="Error: Image generation returned no usable data (no b64_json or url).",
                    break_loop=False,
                )

            return self._build_success_response(prompt, rel_path)

        except Exception as e:
            logger.error(f"[IMAGE_GEN] {provider}/{model} failed: {e}")
            return Response(
                message=f"Error: Image generation failed. {str(e)}",
                break_loop=False,
            )

    async def _generate_openrouter_chat(
        self, litellm_model: str, api_key: str, prompt: str,
        extra_headers: dict, reference_image_path: str | None = None
    ) -> Response:
        """OpenRouter image gen via chat.completions (same endpoint as agents).
        
        OpenRouter serves image gen models through the chat endpoint with
        modalities=["text", "image"], NOT through /images/generations.
        Uses the openai SDK — same as the agent LLM path.
        """
        try:
            from openai import OpenAI
        except ImportError:
            return Response(
                message="Error: openai package is not installed.",
                break_loop=False,
            )

        # Create client with SAME headers agents use
        client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers=extra_headers or {
                "X-Title": "AGIX",
                "HTTP-Referer": "https://example.com",
            },
        )

        # Build content parts
        content_parts = [{"type": "text", "text": prompt}]
        if reference_image_path:
            try:
                ref_content = self._build_reference_content(reference_image_path)
                content_parts.append({
                    "type": "image_url",
                    "image_url": ref_content["image_url"],
                })
                logger.info(f"Image context chain: included reference '{os.path.basename(reference_image_path)}'")
            except Exception as e:
                logger.warning(f"Failed to load reference image, continuing without: {e}")

        # SS-10A: Budget pre-flight check — prevent wasted API calls when budget exhausted
        budget_error = check_openrouter_budget(api_key)
        if budget_error:
            return Response(message=budget_error, break_loop=False)

        # Strip provider prefix for OpenRouter model name
        model_name = litellm_model.replace("openrouter/", "")

        response = client.chat.completions.create(
            model=model_name,
            modalities=["text", "image"],
            messages=[{"role": "user", "content": content_parts}],
        )

        choice = response.choices[0]
        gen_id = self.agent.context.generate_id()
        filename = f"gen_image_{gen_id}.png"
        save_path, rel_path = self._get_storage_paths(filename)
        files.make_dirs(save_path)

        image_saved = False
        text_response = ""

        # Strategy 1: Check message.images
        images_field = getattr(choice.message, "images", None)
        if images_field and isinstance(images_field, list):
            for img in images_field:
                url = None
                if isinstance(img, dict):
                    iu = img.get("image_url", {})
                    url = iu.get("url") if isinstance(iu, dict) else None
                elif hasattr(img, "image_url"):
                    iu = img.image_url
                    url = iu.get("url") if isinstance(iu, dict) else getattr(iu, "url", None)

                if url and url.startswith("data:"):
                    header, b64 = url.split(",", 1)
                    with open(save_path, "wb") as f:
                        f.write(base64.b64decode(b64))
                    image_saved = True
                    break

        # Strategy 2: Check content parts
        if not image_saved:
            content = choice.message.content
            if isinstance(content, list):
                for part in content:
                    ptype = part.get("type", "") if isinstance(part, dict) else getattr(part, "type", "")
                    if ptype == "image_url":
                        iu = part.get("image_url", {}) if isinstance(part, dict) else getattr(part, "image_url", {})
                        url = iu.get("url", "") if isinstance(iu, dict) else getattr(iu, "url", "")
                        if url.startswith("data:"):
                            header, b64 = url.split(",", 1)
                            with open(save_path, "wb") as f:
                                f.write(base64.b64decode(b64))
                            image_saved = True
                            break
                    elif ptype == "text":
                        text_response = part.get("text", "") if isinstance(part, dict) else getattr(part, "text", "")

        if not image_saved:
            msg = "Error: OpenRouter returned no image data in response."
            if text_response:
                msg += f" Model response: {text_response}"
            return Response(message=msg, break_loop=False)

        return self._build_success_response(prompt, rel_path, text_response)

    async def _generate_gemini_native(
        self, model: str, api_key: str, prompt: str,
        reference_image_path: str | None = None
    ) -> Response:
        """Gemini image gen via google-genai SDK.
        
        Gemini requires its own SDK for image gen (response_modalities=["Image"])
        which litellm.image_generation() doesn't support. But the KEY comes
        from the same get_api_key() path as agents.
        """
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            return Response(
                message="Error: google-genai is not installed. Run: pip install google-genai",
                break_loop=False,
            )

        aspect_ratio = self.args.get("aspect_ratio", "1:1")

        client = genai.Client(api_key=api_key)

        # Build contents
        contents = [prompt]
        if reference_image_path:
            try:
                ref_content = self._build_reference_content(reference_image_path)
                ref_part = types.Part.from_bytes(
                    data=base64.b64decode(ref_content["data"]),
                    mime_type=ref_content["mime_type"],
                )
                contents = [prompt, ref_part]
                logger.info(f"Image context chain (Gemini): included reference '{os.path.basename(reference_image_path)}'")
            except Exception as e:
                logger.warning(f"Failed to load reference image for Gemini, continuing without: {e}")

        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=["Image", "Text"],
                image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
            ),
        )

        if not response.parts:
            return Response(
                message="Error: Gemini returned no content. The prompt may have been blocked by safety filters.",
                break_loop=False,
            )

        gen_id = self.agent.context.generate_id()
        filename = f"gen_image_{gen_id}.png"
        save_path, rel_path = self._get_storage_paths(filename)
        files.make_dirs(save_path)

        image_saved = False
        text_response = ""

        for part in response.parts:
            if part.inline_data is not None:
                with open(save_path, "wb") as f:
                    f.write(part.inline_data.data)
                image_saved = True
            elif part.text is not None:
                text_response = part.text

        if not image_saved:
            msg = "Error: Gemini returned no image data."
            if text_response:
                msg += f" Model response: {text_response}"
            return Response(message=msg, break_loop=False)

        return self._build_success_response(prompt, rel_path, text_response)

    def _build_success_response(
        self, prompt: str, rel_path: str, text_response: str = ""
    ) -> Response:
        """Build a standardized success response with image display.
        
        The response is compact to minimize context-window pollution.
        The structured FILENAME:/PATH: block provides unambiguous extraction.
        (RCA: MSR_Smoke_1777469132 — verbose responses wasted ~250 tokens
        per image and contributed to supervisor false-positive nudges.)
        """
        import os
        image_url = f"img://{rel_path}"
        filename = os.path.basename(rel_path)

        message = (
            f"Successfully generated image for prompt: '{prompt}'.\n\n"
            f"![Generated Image]({image_url})\n\n"
            f"---\n"
            f"FILENAME: {filename}\n"
            f"PATH: {rel_path}\n"
            f"---"
        )
        if text_response:
            message += f"\n\n**Model notes:** {text_response}"

        return Response(
            message=message,
            break_loop=False,
            additional={
                "type": "image",
                "path": rel_path,
                "url": image_url,
                "filename": filename,
            },
        )

    def _get_storage_paths(self, filename: str) -> tuple[str, str]:
        """Get absolute save path and relative path for image storage, with project isolation.
        
        Default storage: $PROJECT/docs/design-mockups/ — generated images are
        persistent design artifacts, NOT temp files.
        
        FIX-4: If self._output_path is set, resolves it within the active project
        sandbox and returns that instead of the default path.
        """
        # Canonical persistent directory for generated design assets
        _DEFAULT_IMAGE_DIR = "docs/design-mockups"

        try:
            from python.helpers import projects
        except ImportError as e:
            logger.error(f"Failed to import projects helper: {e}")
            save_dir = files.get_abs_path("tmp")
            rel_path = f"tmp/{filename}"
            return os.path.join(save_dir, filename), rel_path

        project_name = projects.get_context_project_name(self.agent.context)

        # FIX-4: Handle output_path for direct project saves
        output_path = getattr(self, "_output_path", "")
        if output_path and project_name:
            try:
                # ISS-4: Use canonical resolve_agent_path for project root.
                from python.helpers.resolve_agent_path import resolve_agent_path as _rap
                project_dir = _rap("", self.agent)  # Empty path = project root
                resolved = resolve_output_path(output_path, project_dir)
                if resolved:
                    # Build relative path for image_get
                    if resolved.startswith(project_dir):
                        rel_suffix = resolved[len(project_dir):].lstrip("/")
                        rel_path = f"usr/projects/{project_name}/{rel_suffix}"
                    else:
                        rel_path = resolved  # fallback
                    logger.info(f"gen_image output_path resolved: {resolved} (rel: {rel_path})")
                    return resolved, rel_path
            except ValueError as e:
                # Path outside sandbox — log warning but fall back to default
                logger.warning(f"gen_image output_path rejected: {e}. Falling back to {_DEFAULT_IMAGE_DIR}.")
            except Exception as e:
                logger.error(f"gen_image output_path resolution failed: {e}. Falling back to {_DEFAULT_IMAGE_DIR}.")

        if project_name:
            project_folder = projects.get_project_folder(project_name)
            save_dir = os.path.join(project_folder, _DEFAULT_IMAGE_DIR)
            os.makedirs(save_dir, exist_ok=True)
            rel_path = f"usr/projects/{project_name}/{_DEFAULT_IMAGE_DIR}/{filename}"
        else:
            save_dir = files.get_abs_path("tmp")
            rel_path = f"tmp/{filename}"

        save_path = os.path.join(save_dir, filename)
        return save_path, rel_path

