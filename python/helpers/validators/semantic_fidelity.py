import logging
import os

logger = logging.getLogger("agix.validators.semantic")

# Directories/files to skip when scanning for content
_FIDELITY_SKIP_DIRS = {
    "node_modules", ".next", ".nuxt", "dist", "build",
    ".git", "__pycache__", ".agix.proj", ".cache",
}

# File extensions to scan for content fidelity
_FIDELITY_SCAN_EXTENSIONS = {
    ".tsx", ".ts", ".jsx", ".js", ".html", ".vue", ".svelte",
    ".css", ".scss", ".md", ".json",
    ".py", ".go", ".rs", ".rb", ".java", ".kt",  # Universal language support
}

# Minimum match ratio to pass — below this ratio the check blocks
_FIDELITY_MIN_MATCH_RATIO = 0.40

# Maximum blocks before circuit breaker fires
_FIDELITY_MAX_BLOCKS = 3

# ── System 6 Phase 4: Weighted fidelity scoring constants ──────────────

# Term category weights — business-critical terms matter most
_FIDELITY_WEIGHT_BUSINESS = 3.0     # Brand names, URLs, prices, domain
_FIDELITY_WEIGHT_INTEGRATION = 2.0  # API/service names, framework names
_FIDELITY_WEIGHT_CONTENT = 1.0      # Feature names, descriptions, CTAs
_FIDELITY_WEIGHT_BOILERPLATE = -5.0  # Penalty for boilerplate presence

# Boilerplate-risk terms — if found in project, apply penalty
_FIDELITY_BOILERPLATE_TERMS = frozenset([
    "lorem ipsum",
    "create next app",
    "example placeholder",
    "welcome to next.js",
    "get started by editing",
    "learn more about next.js",
    "powered by vercel",
    "my-app",
    "acme inc",
    "john doe",
    "jane doe",
    "your company",
    "sample data",
    "todo: replace",
    "placeholder text",
    "dummy content",
    "test content here",
])

# L2 semantic examination thresholds
_FIDELITY_L2_AMBIGUOUS_LOW = 0.40   # Below this: clear fail (skip L2)
_FIDELITY_L2_AMBIGUOUS_HIGH = 0.70  # Above this: clear pass (skip L2)
_FIDELITY_L2_PASS_THRESHOLD = 0.55  # Semantic similarity to boost to pass
_FIDELITY_L2_FAIL_THRESHOLD = 0.30  # Semantic similarity to confirm fail

# Known integration/API/service patterns (case-insensitive prefixes)
# Used to classify extracted terms as integration (weight 2.0) vs content (1.0)
_KNOWN_INTEGRATION_PATTERNS = frozenset([
    "stripe", "resend", "sendgrid", "twilio", "firebase", "supabase",
    "prisma", "nextauth", "auth0", "clerk", "vercel", "netlify",
    "aws", "azure", "gcp", "cloudflare", "docker", "kubernetes",
    "redis", "mongodb", "postgresql", "mysql", "sqlite",
    "openai", "anthropic", "gemini", "groq", "replicate",
    "tailwind", "shadcn", "radix", "framer", "motion",
    "react", "vue", "svelte", "angular", "next.js", "nuxt",
    "express", "fastapi", "django", "flask", "rails",
    "github", "gitlab", "bitbucket", "jira", "notion",
    "mailchimp", "hubspot", "intercom", "zendesk", "crisp",
    "zapier", "make", "n8n", "webhook",
    "sentry", "datadog", "posthog", "mixpanel", "amplitude",
    "cloudinary", "imgix", "uploadthing", "s3",
    "plaid", "braintree", "paypal", "square", "lemon squeezy",
    "algolia", "meilisearch", "typesense",
])

# Common stop words to exclude from key term extraction
_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "not", "no", "yes", "so", "if", "then", "than", "that", "this",
    "these", "those", "it", "its", "i", "my", "me", "we", "our", "you",
    "your", "he", "she", "they", "them", "their", "what", "which", "who",
    "how", "when", "where", "why", "all", "each", "every", "both",
    "build", "create", "make", "add", "include", "use", "using", "also",
    "page", "site", "website", "web", "app", "application", "project",
    "like", "want", "need", "please", "feature", "features",
    "section", "sections", "component", "components",
}


def _extract_key_terms(prompt: str) -> list:
    """Extract key terms from a user prompt for fidelity checking.

    Extracts:
    - URLs (https://..., http://...)
    - Prices ($X.XX patterns)
    - Multi-word proper nouns (capitalized word sequences)
    - Significant individual words (filtered by stop words, min length 3)

    Returns a deduplicated list of key terms.
    """
    import re

    terms = []

    # Extract URLs
    urls = re.findall(r'https?://[^\s,\'")\]]+', prompt)
    terms.extend(urls)

    # Extract prices ($X, $X.XX, $X,XXX.XX)
    prices = re.findall(r'\$[\d,]+(?:\.\d{1,2})?', prompt)
    terms.extend(prices)

    # Extract quoted strings (likely business names, features)
    quoted = re.findall(r'["\']([^"\']{2,50})["\']', prompt)
    terms.extend(quoted)

    # Extract capitalized multi-word names (e.g., "Bella's Bakery", "AI Analytics")
    # Match sequences of capitalized words including possessives
    proper_nouns = re.findall(r"(?:[A-Z][a-z]+(?:'s)?(?:\s+[A-Z][a-z]+(?:'s)?)+)", prompt)
    terms.extend(proper_nouns)

    # Extract individual significant words
    words = re.findall(r'\b[A-Za-z]{3,}\b', prompt)
    for word in words:
        lower = word.lower()
        if lower not in _STOP_WORDS and len(word) >= 4:
            # Keep capitalized words as-is (likely proper nouns)
            if word[0].isupper():
                terms.append(word)
            elif len(word) >= 5:
                # Longer lowercase words are likely feature names
                terms.append(lower)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for term in terms:
        key = term.lower().strip()
        if key and key not in seen and len(key) >= 2:
            seen.add(key)
            unique.append(term)

    return unique


def _weighted_extract_key_terms(prompt: str) -> dict:
    """Extract key terms from a user prompt, categorized by weight.

    System 6 Phase 4 (ITR-44): Replaces flat term list with weighted
    categories so business-critical terms (brand name, prices, domain)
    score higher than generic feature words.

    Categories:
        business (weight 3.0): URLs, prices, quoted names, multi-word proper nouns
        integration (weight 2.0): Known API/service/framework names
        content (weight 1.0): Feature names, descriptions, CTAs

    Returns:
        dict with keys 'business', 'integration', 'content', each a list of terms.
    """
    import re

    business = []
    integration = []
    content = []

    # ── Business-critical terms (weight 3.0) ──

    # URLs — always business-critical (domain, links)
    urls = re.findall(r'https?://[^\s,\'")\]]+', prompt)
    business.extend(urls)

    # Prices ($X, $X.XX, $X,XXX.XX)
    prices = re.findall(r'\$[\d,]+(?:\.\d{1,2})?', prompt)
    business.extend(prices)

    # Quoted strings (likely business names, product names)
    quoted = re.findall(r'["\']([^"\']{2,50})["\']', prompt)
    business.extend(quoted)

    # Capitalized multi-word names (e.g., "Bella's Bakery", "AI Analytics")
    proper_nouns = re.findall(r"(?:[A-Z][a-z]+(?:'s)?(?:\s+[A-Z][a-z]+(?:'s)?)+)", prompt)
    business.extend(proper_nouns)

    # ── Classify individual words ──

    words = re.findall(r'\b[A-Za-z]{3,}\b', prompt)
    for word in words:
        lower = word.lower()
        if lower in _STOP_WORDS or len(word) < 4:
            continue

        # Check if it's a known integration/API name
        if any(lower == pat or lower.startswith(pat) for pat in _KNOWN_INTEGRATION_PATTERNS):
            integration.append(word)
        elif word[0].isupper():
            # Capitalized word not a known integration — check if it matches
            # a known integration anyway (e.g., "Stripe" → integration)
            if any(lower == pat for pat in _KNOWN_INTEGRATION_PATTERNS):
                integration.append(word)
            else:
                # Proper noun that's not a known service — likely a brand/product
                business.append(word)
        elif len(word) >= 5:
            # Longer lowercase words are likely feature/content terms
            content.append(lower)

    # ── Deduplicate each category ──
    def _dedup(terms):
        seen = set()
        unique = []
        for term in terms:
            key = term.lower().strip()
            if key and key not in seen and len(key) >= 2:
                seen.add(key)
                unique.append(term)
        return unique

    return {
        "business": _dedup(business),
        "integration": _dedup(integration),
        "content": _dedup(content),
    }


def _calculate_weighted_fidelity_score(
    weighted_terms: dict,
    project_content: str,
) -> tuple:
    """Calculate weighted content fidelity score.

    System 6 Phase 4 (ITR-44): Scoring formula that weights business-critical
    terms higher than generic content, and applies penalty for boilerplate.

    Formula:
        weighted_found = sum(weight * count_found_in_category)
        weighted_total = sum(weight * count_total_in_category)
        boilerplate_penalty = BOILERPLATE_WEIGHT * count_boilerplate_found
        score = (weighted_found + boilerplate_penalty) / weighted_total

    Args:
        weighted_terms: Dict from _weighted_extract_key_terms with
                        'business', 'integration', 'content' keys.
        project_content: Concatenated source code content string.

    Returns:
        Tuple of (score: float or None, details: dict).
        score is None if no terms exist (skip check).
    """
    weights = {
        "business": _FIDELITY_WEIGHT_BUSINESS,
        "integration": _FIDELITY_WEIGHT_INTEGRATION,
        "content": _FIDELITY_WEIGHT_CONTENT,
    }

    total_terms = sum(len(weighted_terms.get(cat, [])) for cat in weights)
    if total_terms == 0:
        return None, {"matched": [], "missing": [], "boilerplate_found": 0}

    content_lower = project_content.lower()
    matched = []
    missing = []
    weighted_found = 0.0
    weighted_total = 0.0

    for category, weight in weights.items():
        terms = weighted_terms.get(category, [])
        for term in terms:
            weighted_total += weight
            if term.lower() in content_lower:
                matched.append((term, category, weight))
                weighted_found += weight
            else:
                missing.append((term, category, weight))

    # ── Boilerplate penalty ──
    boilerplate_count = 0
    for bp_term in _FIDELITY_BOILERPLATE_TERMS:
        if bp_term in content_lower:
            boilerplate_count += 1

    boilerplate_penalty = _FIDELITY_WEIGHT_BOILERPLATE * boilerplate_count

    # Calculate final score (clamp to [0, 1])
    if weighted_total > 0:
        raw_score = (weighted_found + boilerplate_penalty) / weighted_total
        score = max(0.0, min(1.0, raw_score))
    else:
        score = 0.0

    details = {
        "matched": matched,
        "missing": missing,
        "boilerplate_found": boilerplate_count,
        "weighted_found": weighted_found,
        "weighted_total": weighted_total,
        "boilerplate_penalty": boilerplate_penalty,
        "raw_score": raw_score if weighted_total > 0 else 0.0,
    }

    return score, details


def _l2_semantic_content_fidelity(
    manifest_terms: list,
    project_content: str,
    cache: dict = None,
) -> float:
    """Layer 2: Semantic similarity between manifest requirements and project content.

    System 6 Phase 4 (ITR-44): Uses the in-memory sentence-transformers model
    (all-MiniLM-L6-v2, 22M params) for semantic similarity when Layer 1
    (keyword matching) produces an ambiguous score (40-70%).

    ~20-30ms total (2 embeddings + 1 dot product). No API call. No added cost.

    Args:
        manifest_terms: List of key terms from the manifest/prompt.
        project_content: Concatenated source code content string.
        cache: Optional dict to cache results by content hash (agent_data).

    Returns:
        float: Cosine similarity score in [0, 1], or -1.0 if model unavailable.
    """
    import hashlib

    # Build cache key from content hash
    manifest_text = " ".join(str(t) for t in manifest_terms)[:512]
    content_snippet = project_content[:2048]
    cache_key = hashlib.sha256(
        (manifest_text + "||" + content_snippet).encode("utf-8", errors="ignore")
    ).hexdigest()[:16]

    # Check cache
    if cache is not None:
        cached = cache.get("_l2_fidelity_cache", {}).get(cache_key)
        if cached is not None:
            return cached

    # Attempt semantic embedding
    try:
        from python.helpers.semantic_embeddings import (
            compute_embedding_sync,
            cosine_similarity,
        )
    except ImportError:
        logger.debug("[FIDELITY L2] semantic_embeddings module not available")
        return -1.0

    manifest_emb = compute_embedding_sync(manifest_text, max_chars=512)
    if manifest_emb is None:
        return -1.0

    content_emb = compute_embedding_sync(content_snippet, max_chars=512)
    if content_emb is None:
        return -1.0

    score = cosine_similarity(manifest_emb, content_emb)

    # Store in cache
    if cache is not None:
        cache.setdefault("_l2_fidelity_cache", {})[cache_key] = score

    return score


def _scan_project_content(project_dir: str) -> str:
    """Read all scannable source files and concatenate their content.

    Returns a single string with all file contents for term matching.
    """
    all_content = []
    for root, dirs, files in os.walk(project_dir):
        # Prune irrelevant directories
        dirs[:] = [d for d in dirs if d not in _FIDELITY_SKIP_DIRS]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _FIDELITY_SCAN_EXTENSIONS:
                continue
            filepath = os.path.join(root, fname)
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                all_content.append(content)
            except (IOError, OSError):
                continue

    return "\n".join(all_content)


def check_semantic_fidelity(project_dir: str, prompt: str, cache: dict = None) -> dict:
    """Reusable validator for Semantic Content Fidelity.
    
    Can be used by both the Orchestrator (via content.py) and the Code Agent 
    (via self_check_suite.py) to verify that the project's source code 
    contains the required business logic, integrations, and content terms 
    from the original prompt.
    
    Args:
        project_dir: Path to the project root
        prompt: The original user prompt
        cache: Optional agent_data dict for caching L2 embeddings
        
    Returns:
        A structured dict with validation results, including passed (bool),
        score (float), and reasons (list of strings).
    """
    if not project_dir or not prompt:
        return {"passed": True, "score": 1.0, "reasons": ["Missing prompt or project dir"]}
        
    terms = _weighted_extract_key_terms(prompt)
    project_content = _scan_project_content(project_dir)
    
    score, details = _calculate_weighted_fidelity_score(terms, project_content)
    
    if score is None:
        return {"passed": True, "score": 1.0, "reasons": ["No scorable terms found in prompt."]}

    passed = score >= _FIDELITY_MIN_MATCH_RATIO
    reasons = []
    
    # Layer 2 fallback
    l2_score = -1.0
    if not passed and score >= _FIDELITY_L2_AMBIGUOUS_LOW:
        l2_score = _l2_semantic_content_fidelity(
            manifest_terms=terms.get("business", []) + terms.get("integration", []) + terms.get("content", []),
            project_content=project_content,
            cache=cache,
        )
        if l2_score >= _FIDELITY_L2_PASS_THRESHOLD:
            passed = True
            reasons.append(f"L2 Semantic Override: Similar content detected (L2 score: {l2_score:.2f})")
    
    if not passed:
        missing_business = [t[0] for t in details.get("missing", []) if t[1] == "business"]
        missing_integration = [t[0] for t in details.get("missing", []) if t[1] == "integration"]
        
        if missing_business:
            reasons.append(f"Missing core business terms: {', '.join(missing_business[:3])}")
        if missing_integration:
            reasons.append(f"Missing integration terms: {', '.join(missing_integration[:3])}")
            
        reasons.append(f"Content fidelity score ({score:.2f}) below threshold ({_FIDELITY_MIN_MATCH_RATIO})")
        if l2_score >= 0:
            reasons.append(f"L2 Semantic score ({l2_score:.2f}) also below threshold ({_FIDELITY_L2_PASS_THRESHOLD})")
            
    else:
        reasons.append(f"Content fidelity check passed (L1 score: {score:.2f})")
        
    return {
        "passed": passed,
        "score": score,
        "l2_score": l2_score,
        "reasons": reasons,
        "details": details
    }


# ── Per-File Semantic Content Quality ────────────────────────────────────
#
# Universal, language-agnostic content quality check for individual files.
# Uses embedding similarity between file content and its requirement spec.
# Replaces the line-count heuristic (MIN_PAGE_LINES) with semantic analysis.
# (SS-2, MainStreet ITR-44 RCA)

# Minimum similarity between file content and requirement to pass
_FILE_QUALITY_MIN_SIMILARITY = 0.30

# Minimum content length in bytes — below this is trivially empty
_FILE_QUALITY_MIN_BYTES = 50


def check_file_content_quality(
    file_path: str,
    requirement_text: str,
    min_similarity: float = _FILE_QUALITY_MIN_SIMILARITY,
) -> dict:
    """Universal semantic content quality check for a single file.

    2-layer detection architecture (ADR-082):
      Layer 1 (fast): File exists, non-empty, supported extension,
                      no boilerplate terms.
      Layer 2 (semantic): Compute embedding similarity between file content
                          and requirement text.

    Works for ANY file type in _FIDELITY_SCAN_EXTENSIONS (.tsx, .py, .go, etc.).

    Args:
        file_path: Absolute path to the file to check.
        requirement_text: The requirement/BDD text this file should implement.
        min_similarity: Minimum cosine similarity to pass (default 0.30).

    Returns:
        Dict with keys:
          - quality_pass (bool): Whether the file passes quality check.
          - similarity (float): Cosine similarity score (-1.0 if model unavailable).
          - reason (str): Why it passed/failed.
          - has_boilerplate (bool): Whether boilerplate terms were detected.
    """
    # ── Layer 1: Fast deterministic checks ──

    # Check file exists
    if not os.path.isfile(file_path):
        return {
            "quality_pass": False,
            "similarity": 0.0,
            "reason": "file_not_found",
            "has_boilerplate": False,
        }

    # Check extension
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in _FIDELITY_SCAN_EXTENSIONS:
        return {
            "quality_pass": False,
            "similarity": 0.0,
            "reason": "unsupported_extension",
            "has_boilerplate": False,
        }

    # Read file content
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except (IOError, OSError) as e:
        return {
            "quality_pass": False,
            "similarity": 0.0,
            "reason": f"read_error: {e}",
            "has_boilerplate": False,
        }

    # Check empty/trivial
    if len(content.strip()) < _FILE_QUALITY_MIN_BYTES:
        return {
            "quality_pass": False,
            "similarity": 0.0,
            "reason": "empty_file",
            "has_boilerplate": False,
        }

    # Check boilerplate
    content_lower = content.lower()
    boilerplate_found = [
        term for term in _FIDELITY_BOILERPLATE_TERMS
        if term in content_lower
    ]
    has_boilerplate = len(boilerplate_found) > 0

    # ── Layer 2: Semantic embedding similarity ──
    try:
        from python.helpers.semantic_embeddings import (
            compute_embedding_sync,
            cosine_similarity,
        )
    except ImportError:
        logger.debug("[FILE QUALITY] semantic_embeddings not available")
        # Graceful degradation: if embeddings unavailable, use boilerplate check only
        return {
            "quality_pass": not has_boilerplate,
            "similarity": -1.0,
            "reason": "embedding_unavailable" if not has_boilerplate else "boilerplate_detected",
            "has_boilerplate": has_boilerplate,
        }

    # Compute embeddings (truncates to first 512 chars — core semantics)
    content_emb = compute_embedding_sync(content, max_chars=512)
    req_emb = compute_embedding_sync(requirement_text, max_chars=512)

    if content_emb is None or req_emb is None:
        # Model unavailable — graceful degradation
        return {
            "quality_pass": not has_boilerplate,
            "similarity": -1.0,
            "reason": "embedding_failed" if not has_boilerplate else "boilerplate_detected",
            "has_boilerplate": has_boilerplate,
        }

    similarity = cosine_similarity(content_emb, req_emb)

    # Apply boilerplate penalty
    effective_similarity = similarity
    if has_boilerplate:
        # Each boilerplate term reduces similarity by 0.1
        penalty = min(len(boilerplate_found) * 0.1, 0.3)
        effective_similarity = max(0.0, similarity - penalty)
        logger.debug(
            f"[FILE QUALITY] Boilerplate penalty: {penalty:.2f} "
            f"({len(boilerplate_found)} terms: {boilerplate_found[:3]})"
        )

    quality_pass = effective_similarity >= min_similarity and not has_boilerplate

    # Build reason
    if quality_pass:
        reason = f"semantic_match (similarity={effective_similarity:.3f})"
    elif has_boilerplate:
        reason = "boilerplate_detected"
    else:
        reason = f"low_similarity (similarity={effective_similarity:.3f}, threshold={min_similarity})"

    return {
        "quality_pass": quality_pass,
        "similarity": effective_similarity,
        "reason": reason,
        "has_boilerplate": has_boilerplate,
    }
