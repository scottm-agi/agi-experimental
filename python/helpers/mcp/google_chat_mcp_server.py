import os
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from mcp.server.fastmcp import FastMCP

# Configure logging
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("google_chat_mcp")

DEPENDENCIES_LOADED = True
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
except ImportError as e:
    DEPENDENCIES_LOADED = False
    DEPENDENCY_ERROR = str(e)
    logger.error(f"Missing Google API dependencies: {e}")
    logger.error("Please ensure 'google-api-python-client', 'google-auth-oauthlib', and 'google-auth-httplib2' are installed.")

# If modifying these SCOPES, delete the file token.json.
SCOPES = [
    'https://www.googleapis.com/auth/chat.messages', 
    'https://www.googleapis.com/auth/chat.spaces.readonly', 
    'https://www.googleapis.com/auth/chat.memberships.readonly',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/contacts.other.readonly',  # People API for sender name resolution
]

mcp = FastMCP("Google Chat")

REGISTRY_FILE = "/agix/data/google_chat_registry.json" if os.path.exists("/agix/data") else "/agix/data/google_chat_registry.json"

def load_registry() -> Dict[str, Any]:
    if os.path.exists(REGISTRY_FILE):
        with open(REGISTRY_FILE, "r") as f:
            return json.load(f)
    return {"spaces": {}, "threads": {}}

def save_registry(registry: Dict[str, Any]):
    dirname = os.path.dirname(REGISTRY_FILE)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    with open(REGISTRY_FILE, "w") as f:
        json.dump(registry, f, indent=4)

def get_chat_service():
    if not DEPENDENCIES_LOADED:
        raise Exception(f"Google Chat MCP dependencies are missing: {DEPENDENCY_ERROR}. Please install 'google-api-python-client', 'google-auth-oauthlib', and 'google-auth-httplib2'.")

    from python.helpers.secrets_helper import get_default_secrets_manager
    sm = get_default_secrets_manager()
    
    # Try to load credentials from secrets
    secrets = sm.load_secrets()
    token_json = secrets.get("GOOGLE_CHAT_TOKEN")
    
    # Fallback: check filesystem for token.json (matches UI behavior)
    TOKEN_FILE_PATH = "/agix/token.json" if os.path.exists("/agix/token.json") else "/agix/token.json"
    if not token_json and os.path.exists(TOKEN_FILE_PATH):
        try:
            with open(TOKEN_FILE_PATH, "r") as f:
                token_json = f.read()
            logger.info("Loaded token from filesystem fallback")
        except Exception as e:
            logger.warning(f"Failed to read token file: {e}")
    
    creds = None
    if token_json:
        try:
            creds_data = json.loads(token_json)
            # HARDENING: Ensure we have all required fields for from_authorized_user_info
            if isinstance(creds_data, dict) and ('token' in creds_data or 'refresh_token' in creds_data):
                token_scopes = creds_data.get('scopes', SCOPES)
                creds = Credentials.from_authorized_user_info(creds_data, token_scopes)
                logger.debug("Initialized Google credentials from storage")
            else:
                logger.warning("GOOGLE_CHAT_TOKEN JSON missing 'token' or 'refresh_token' fields")
        except Exception as e:
            logger.error(f"Failed to parse GOOGLE_CHAT_TOKEN JSON: {e}")
        
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                # Persist the refreshed token
                sm.set_secret("GOOGLE_CHAT_TOKEN", creds.to_json())
            except Exception as e:
                logger.error(f"Failed to refresh Google Chat token: {e}")
                raise Exception("Google Chat token is expired and could not be refreshed. Please re-authenticate via the Settings UI.")
        else:
            raise Exception("Google Chat connection is not active. Please visit the Settings UI > OAuth tab to 'Connect Google Chat'.")

    return build('chat', 'v1', credentials=creds, cache_discovery=False)

def normalize_space_id(space_id: str) -> str:
    """Normalize space_id by stripping 'spaces/' prefix if present.
    
    The API returns names like 'spaces/AAQAeYvsvFY' but expects just the ID
    when constructing parent paths. This handles both formats.
    """
    if space_id.startswith("spaces/"):
        return space_id[7:]  # Remove 'spaces/' prefix
    return space_id

# Cache for resolved user names (bounded to prevent memory leak)
_CACHE_MAX_SIZE = 256
_global_user_map: Dict[str, Dict[str, Any]] = {}
_user_name_cache: Dict[str, str] = {}

def _cache_sender(user_id: str, name: str) -> None:
    """Store a resolved sender name, evicting oldest entries if cache is full."""
    if len(_user_name_cache) >= _CACHE_MAX_SIZE:
        to_remove = list(_user_name_cache.keys())[:_CACHE_MAX_SIZE // 2]
        for k in to_remove:
            del _user_name_cache[k]
    _user_name_cache[user_id] = name

def _load_user_map_if_needed():
    """Load user map from data/google_chat_users.json on first call."""
    if _global_user_map:
        return  # Already loaded
    try:
        import os
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'data')
        user_map_file = os.path.join(data_dir, 'google_chat_users.json')
        
        user_map = {}
        if os.path.exists(user_map_file):
            with open(user_map_file, 'r') as f:
                user_map = json.load(f)
        else:
            # Fallback to env var
            user_map_json = os.environ.get('GOOGLE_CHAT_USER_MAP', '{}')
            user_map = json.loads(user_map_json)
            
        for uid, data in user_map.items():
            full_key = uid if uid.startswith('users/') else f'users/{uid}'
            if isinstance(data, str):
                # Simple name mapping
                _global_user_map[full_key] = {"displayName": data, "emails": []}
                _user_name_cache[full_key] = data
            elif isinstance(data, dict):
                # Rich object mapping
                _global_user_map[full_key] = data
                _user_name_cache[full_key] = data.get('displayName', 'Unknown')
                
        if _global_user_map:
            logger.info(f"Loaded {len(_global_user_map)} user mappings")
    except Exception as e:
        logger.warning(f"Failed to load user map: {e}")


def resolve_sender_name(service, space_id: str, sender: Dict[str, Any]) -> str:
    """Resolve display name for a sender using multiple strategies.
    
    Resolution order:
    1. In-memory cache (fastest)
    2. User map file (data/google_chat_users.json)
    3. Chat API displayName from sender object
    4. Bot identifier formatting (for BOT users)
    5. Memberships API (space-level, cached per space) — most reliable for workspace users
    6. People API lookup (for HUMAN users) — requires people.readonly scope
    7. Raw user ID fallback
    """
    user_name = sender.get('name', '')
    sender_type = sender.get('type', 'HUMAN')
    
    # 1. Check cache first
    if user_name and user_name in _user_name_cache:
        return _user_name_cache[user_name]
    
    # 2. Check user map file
    _load_user_map_if_needed()
    if user_name and user_name in _user_name_cache:
        return _user_name_cache[user_name]
    
    # 3. Check if Chat API already provided displayName
    display_name = sender.get('displayName')
    if display_name and display_name != 'Unknown':
        if user_name:
            _cache_sender(user_name, display_name)
        return display_name
    
    if not user_name:
        return 'Unknown'
    
    # 4. For BOT type, format a readable identifier
    if sender_type == 'BOT':
        short_id = user_name.replace('users/', '') if user_name else 'unknown'
        bot_name = f"Bot ({short_id[:12]})"
        _cache_sender(user_name, bot_name)
        return bot_name
    
    # 5. Memberships API (most reliable for workspace users) — cache per space
    try:
        _resolve_space_members(service, space_id)
        if user_name in _user_name_cache:
            return _user_name_cache[user_name]
    except Exception as e:
        logger.debug(f"Memberships lookup failed for space {space_id}: {e}")
    
    # 6. People API fallback (requires people.readonly scope)
    try:
        person_id = user_name.replace('users/', 'people/')
        people_service = build('people', 'v1', credentials=service._http.credentials, cache_discovery=False)
        person = people_service.people().get(
            resourceName=person_id,
            personFields='names,emailAddresses'
        ).execute()
        
        names = person.get('names', [])
        if names:
            resolved = names[0].get('displayName', user_name)
            _cache_sender(user_name, resolved)
            logger.info(f"People API resolved {user_name} -> {resolved}")
            return resolved
        emails = person.get('emailAddresses', [])
        if emails:
            resolved = emails[0].get('value', user_name)
            _cache_sender(user_name, resolved)
            logger.info(f"People API email fallback {user_name} -> {resolved}")
            return resolved
    except Exception as e:
        logger.debug(f"People API lookup failed for {user_name}: {e}")
    
    # 7. Final fallback: readable user ID
    short_id = user_name.replace('users/', 'User_')
    _cache_sender(user_name, short_id)
    return short_id


# Track which spaces have had their memberships resolved
_resolved_spaces: set = set()

def _resolve_space_members(service, space_id: str):
    """Resolve all members of a space at once and cache their display names.
    
    This avoids N+1 queries when listing multiple messages from the same space.
    """
    global _resolved_spaces
    
    if space_id in _resolved_spaces:
        return  # Already resolved this space
    
    try:
        results = service.spaces().members().list(parent=f"spaces/{space_id}").execute()
        memberships = results.get('memberships', [])
        logger.warning(f"[MEMBERSHIP] Space {space_id}: found {len(memberships)} members")
        for m in memberships:
            member = m.get('member', {})
            member_name = member.get('name', '')
            display = member.get('displayName', '')
            logger.warning(f"[MEMBERSHIP] Raw member: name={member_name}, displayName={display}, keys={list(member.keys())}")
            if member_name and display:
                _cache_sender(member_name, display)
                logger.info(f"Membership resolved {member_name} -> {display}")
        
        _resolved_spaces.add(space_id)
    except Exception as e:
        logger.warning(f"Space membership resolution failed for {space_id}: {e}")
        _resolved_spaces.add(space_id)  # Don't retry failed spaces

@mcp.tool()
def google_chat_list_spaces(page_size: int = 25, page_token: Optional[str] = None) -> str:
    """List available Google Chat spaces with pagination.
    
    Returns a pre-formatted text listing of spaces with their names and IDs.
    Use page_token from the response to get the next page if available.
    
    IMPORTANT: Present the returned text to the user EXACTLY as-is.
    Do NOT rewrite, substitute, or fabricate any space names or IDs.
    
    Args:
        page_size: Number of spaces per page (default 25, max 100).
        page_token: Token for next page of results (from previous call).
    
    Returns:
        Pre-formatted text with space names and IDs. 
    """
    service = get_chat_service()
    
    params = {"pageSize": min(page_size, 100)}
    if page_token:
        params["pageToken"] = page_token
    
    results = service.spaces().list(**params).execute()
    spaces = results.get('spaces', [])
    next_page = results.get('nextPageToken')
    
    if not spaces:
        return "No Google Chat spaces found."
    
    # Build pre-formatted text output (matches list_messages pattern)
    lines = [f"Google Chat Spaces ({len(spaces)} results):"]
    lines.append("")
    
    for space in spaces:
        name = space.get('displayName', space.get('name', 'Unknown'))
        space_id = space.get('name', 'N/A')
        space_type = space.get('type', 'UNKNOWN')
        # membershipCount may be an int or nested dict
        mc_raw = space.get('membershipCount', None)
        if isinstance(mc_raw, dict):
            member_count = mc_raw.get('joinedDirectHumanUserCount', '?')
        elif mc_raw is not None:
            member_count = mc_raw
        else:
            member_count = '?'
        
        lines.append(f"- {name}  |  ID: {space_id}  |  Type: {space_type}  |  Members: {member_count}")
    
    lines.append("")
    if next_page:
        lines.append(f"--- More spaces available. Use page_token='{next_page}' to get next page. ---")
    else:
        lines.append("--- End of spaces list ---")
    
    return "\n".join(lines)


@mcp.tool()
def google_chat_register_space(space_id: str, friendly_name: str) -> str:
    """Register a space ID with a friendly name in the local registry."""
    registry = load_registry()
    registry["spaces"][friendly_name] = space_id
    save_registry(registry)
    return f"Space '{friendly_name}' registered with ID '{space_id}'"

@mcp.tool()
def google_chat_list_registry() -> Dict[str, Any]:
    """List all registered spaces and threads."""
    return load_registry()

@mcp.tool()
def google_chat_send_message(space_id: str, text: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
    """Send a message to a space or thread. If thread_id is provided, it replies to that thread."""
    space_id = normalize_space_id(space_id)
    service = get_chat_service()
    message = {'text': text}
    if thread_id:
        message['thread'] = {'name': f"spaces/{space_id}/threads/{thread_id}"}
    
    result = service.spaces().messages().create(
        parent=f"spaces/{space_id}",
        body=message
    ).execute()
    return result

@mcp.tool()
def google_chat_list_messages(
    space_id: str, 
    page_size: int = 20, 
    order_by: str = "createTime desc", 
    page_token: Optional[str] = None,
    filter: Optional[str] = None
) -> str:
    """List messages in a space with sorting and pagination.
    
    IMPORTANT: Present the returned text to the user EXACTLY as-is.
    Do NOT rewrite, summarize, or fabricate any message content.
    
    Args:
        space_id: The ID of the space.
        page_size: Number of messages to retrieve (max 1000).
        order_by: How to order the messages (e.g., 'createTime desc' for newest first).
        page_token: Token for the next page of results.
        filter: A query filter. Example: 'createTime > "2024-01-01T00:00:00Z"'.
    
    Returns:
        Pre-formatted text with messages. Use google_chat_get_message for full details on a specific message.
    """
    space_id = normalize_space_id(space_id)
    service = get_chat_service()
    
    params = {
        "parent": f"spaces/{space_id}",
        "pageSize": page_size,
        "orderBy": order_by
    }
    if page_token:
        params["pageToken"] = page_token
    if filter:
        params["filter"] = filter
        
    results = service.spaces().messages().list(**params).execute()
    messages = results.get('messages', [])
    if not messages:
        return f"No messages found in space {space_id} matching the criteria."
    
    next_page = results.get('nextPageToken')
    
    # Build pre-formatted text output
    lines = [f"Messages from space {space_id} ({len(messages)} results):"]
    
    for msg in messages:
        sender_display = 'Unknown'
        if 'sender' in msg:
            sender_display = resolve_sender_name(service, space_id, msg['sender'])
        
        time_str = msg.get('createTime', '')[:16].replace('T', ' ')
        text = msg.get('text', '').replace('\n', ' ').strip()
        if len(text) > 120:
            text = text[:120] + '...'
        msg_id = msg.get('name', '')
        
        lines.append(f"- [{time_str}] {sender_display}: {text}  (ID: {msg_id})")
    
    if next_page:
        lines.append(f"--- More results available. Use page_token='{next_page}' to get next page. ---")
    else:
        lines.append("--- End of results ---")
    
    return "\n".join(lines)

@mcp.tool()
def google_chat_get_message(message_name: str) -> Dict[str, Any]:
    """Get full details of a single message by its resource name (e.g., 'spaces/AAA/messages/BBB').
    
    CRITICAL: When presenting message content to users, you MUST quote the exact text 
    verbatim. NEVER paraphrase, summarize, or modify the message content in any way.
    Use blockquotes (>) to clearly indicate the original message text.
    
    Use this if a large message list was summarized and you need to see the full content or metadata of a specific message.
    
    Returns:
        Message with 'text' field containing the EXACT original content. 
        Present this text VERBATIM in your response - do not reword or interpret it.
    """
    service = get_chat_service()
    message = service.spaces().messages().get(name=message_name).execute()
    
    # Resolve sender name
    if 'sender' in message:
        # Extract space_id from message_name (spaces/SPA/messages/MSG)
        parts = message_name.split('/')
        if len(parts) >= 2:
            space_id = parts[1]
            message['sender']['displayName'] = resolve_sender_name(service, space_id, message['sender'])
            
    return message

@mcp.tool()
def google_chat_search_messages(space_id: str, query_filter: str, page_size: int = 10) -> str:
    """Search for messages in a space using a filter.
    
    IMPORTANT: Present the returned text to the user EXACTLY as-is.
    Do NOT rewrite, summarize, or fabricate any message content.
    
    Example filters:
    - 'createTime > "2024-05-15T00:00:00Z"' (messages after a date)
    - 'thread.name = "spaces/SPACE_ID/threads/THREAD_ID"' (messages in a thread)
    
    Returns:
        Pre-formatted text with matching messages.
    """
    space_id = normalize_space_id(space_id)
    service = get_chat_service()
    try:
        results = service.spaces().messages().list(
            parent=f"spaces/{space_id}",
            filter=query_filter,
            pageSize=page_size,
            orderBy="createTime desc"
        ).execute()
    except Exception as e:
        if "403" in str(e):
            return f"Permission Denied: Access to space {space_id} is restricted by organizational policy or the app is not a participant. If this is a Direct Message or restricted space, the app may lack necessary permissions to list messages."
        raise e
    messages = results.get('messages', [])
    if not messages:
        return f"No messages found in space {space_id} matching filter: {query_filter}"
    
    # Build pre-formatted text output
    lines = [f"Found {len(messages)} messages in space {space_id}:"]
    
    for msg in messages:
        sender_display = 'Unknown'
        if 'sender' in msg:
            sender_display = resolve_sender_name(service, space_id, msg['sender'])
        
        time_str = msg.get('createTime', '')[:16].replace('T', ' ')
        text = msg.get('text', '').replace('\n', ' ').strip()
        if len(text) > 120:
            text = text[:120] + '...'
        msg_id = msg.get('name', '')
        
        lines.append(f"- [{time_str}] {sender_display}: {text}  (ID: {msg_id})")
    
    lines.append("--- End of results ---")
    return "\n".join(lines)


@mcp.tool()
def google_chat_create_thread_reply(space_id: str, thread_id: str, text: str) -> Dict[str, Any]:
    """Creates a reply in a specific thread, ensuring nested thread integrity."""
    space_id = normalize_space_id(space_id)
    return google_chat_send_message(space_id, text, thread_id)

@mcp.tool()
def google_chat_get_connection_status() -> Dict[str, Any]:
    """Check the current connection status and dependency health of the Google Chat MCP."""
    status = {
        "dependencies_ok": DEPENDENCIES_LOADED,
        "dependency_error": DEPENDENCY_ERROR if not DEPENDENCIES_LOADED else None,
        "token_present": False,
        "authenticated": False,
        "error": None
    }
    
    if not DEPENDENCIES_LOADED:
        return status
        
    try:
        from python.helpers.secrets_helper import get_default_secrets_manager
        sm = get_default_secrets_manager()
        secrets = sm.load_secrets()
        token_json = secrets.get("GOOGLE_CHAT_TOKEN")
        
        # Fallback check
        TOKEN_FILE_PATH = "/agix/token.json" if os.path.exists("/agix/token.json") else "/agix/token.json"
        if token_json or os.path.exists(TOKEN_FILE_PATH):
            status["token_present"] = True
            
        service = get_chat_service()
        if service:
            status["authenticated"] = True
    except Exception as e:
        status["error"] = str(e)
        
    return status

# ============================================================
# Server-side search cache for paginated cross-space searches
# ============================================================
_search_cache: Dict[str, Dict[str, Any]] = {}  # hash -> {"results": [...], "timestamp": float}
_SEARCH_CACHE_TTL = 300  # 5 minutes

def _cache_key(since_hours: int, sender_name: Optional[str]) -> str:
    """Generate a stable cache key for search params."""
    from python.helpers.hashing import content_hash
    raw = f"{since_hours}:{(sender_name or '').lower()}"
    return content_hash(raw)

def _get_or_run_search(since_hours: int, sender_name: Optional[str], max_per_space: int) -> Dict[str, Any]:
    """Run cross-space search and cache results, or return cached results with metadata."""
    import time as _time
    from datetime import datetime, timezone, timedelta
    
    key = _cache_key(since_hours, sender_name)
    now = _time.time()
    
    # Check cache
    if key in _search_cache and (now - _search_cache[key]["timestamp"]) < _SEARCH_CACHE_TTL:
        return _search_cache[key]
    
    # Run fresh search
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    cutoff_str = cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    service = get_chat_service()
    all_spaces = service.spaces().list().execute().get('spaces', [])
    
    all_messages = []
    restricted_spaces = []
    
    for space in all_spaces:
        sid = space['name'].replace('spaces/', '')
        space_name = space.get('displayName', 'Unnamed')
        
        try:
            try:
                params = {
                    "parent": f"spaces/{sid}",
                    "pageSize": max_per_space,
                    "orderBy": "createTime desc",
                    "filter": f'createTime > "{cutoff_str}"'
                }
                api_result = service.spaces().messages().list(**params).execute()
                messages = api_result.get('messages', [])
            except Exception as e:
                if "403" in str(e):
                    logger.warning(f"Permission denied for space {space_name} ({sid}): {e}")
                    restricted_spaces.append(f"{space_name} ({sid})")
                    continue
                raise e
            
            if not messages:
                continue
            
            for msg in messages:
                sender_display = 'Unknown'
                if 'sender' in msg:
                    sender_display = resolve_sender_name(service, sid, msg['sender'])
                
                # Filter by sender if specified
                if sender_name and sender_name.lower() not in sender_display.lower():
                    continue
                
                all_messages.append({
                    "text": msg.get("text", ""),
                    "sender": sender_display,
                    "createTime": msg.get("createTime", ""),
                    "name": msg.get("name", ""),
                    "thread": msg.get("thread", {}).get("name", ""),
                    "space_name": space_name,
                    "space_id": sid,
                })
        except Exception as e:
            logger.warning(f"Error searching space {space_name} ({sid}): {e}")
            continue
    
    # Sort by time descending
    all_messages.sort(key=lambda m: m.get('createTime', ''), reverse=True)
    
    # Store complete result set in cache
    result_data = {
        "results": all_messages, 
        "restricted_spaces": restricted_spaces,
        "timestamp": now
    }
    _search_cache[key] = result_data
    
    # Evict old cache entries
    expired = [k for k, v in _search_cache.items() if (now - v["timestamp"]) > _SEARCH_CACHE_TTL]
    for k in expired:
        del _search_cache[k]
    
    return result_data

@mcp.tool()
def google_chat_search_all_spaces(
    output_dir: str,
    since_hours: int = 24,
    sender_name: Optional[str] = None,
    max_per_space: int = 50,
    page: int = 1,
    page_size: int = 10,
) -> str:
    """Search messages across ALL Google Chat spaces with pagination.
    
    Writes one markdown file PER SPACE into output_dir/google_chat/ for all
    messages in the current result set. Returns a paginated manifest.
    
    Args:
        output_dir: Directory for result files (e.g. workspace path).
        since_hours: How far back to search (default 24h).
        sender_name: Optional partial name to filter by.
        max_per_space: Max messages per space to fetch from API.
        page: Current page of results in the manifest.
        page_size: How many space summaries per page in the manifest.
    """
    try:
        search_data = _get_or_run_search(since_hours, sender_name, max_per_space)
        all_messages = search_data["results"]
        restricted = search_data.get("restricted_spaces", [])
    except Exception as e:
        return f"Error searching spaces: {e}"
    
    if not all_messages and not restricted:
        return f"No messages found (last {since_hours}h, sender={sender_name or 'all'})"
    
    # Group by space
    by_space: Dict[str, list] = {}
    for msg in all_messages:
        space = msg.get('space_name', 'Unknown')
        by_space.setdefault(space, []).append(msg)
    
    chat_dir = os.path.join(output_dir, 'google_chat')
    os.makedirs(chat_dir, exist_ok=True)
    
    space_results = []
    # Sort spaces by importance (message count)
    for space_name, msgs in sorted(by_space.items(), key=lambda x: -len(x[1])):
        try:
            # Sanitize space name for filename
            safe_name = space_name.replace(' ', '_').replace('/', '_').replace('+', '_')
            safe_name = ''.join(c for c in safe_name if c.isalnum() or c in '_-')
            filepath = os.path.join(chat_dir, f"{safe_name}.md")
            
            with open(filepath, 'w') as f:
                f.write(f"# {space_name} — Chat Messages\n\n")
                f.write(f"**Filter**: Last {since_hours}h, sender={sender_name or 'all'}\n")
                f.write(f"**Messages**: {len(msgs)}\n\n---\n\n")
                
                for msg in msgs:
                    time_str = msg.get('createTime', '')[:16].replace('T', ' ')
                    sender = msg.get('sender', 'Unknown')
                    text = msg.get('text', '').strip()
                    f.write(f"**[{time_str}] {sender}:**\n> {text}\n\n")
            
            # Create display path
            # This ensures the agent uses the unified /agix/ prefix regardless of environment
            display_path = filepath
            if '/agix/' in filepath:
                display_path = '/agix/' + filepath.split('/agix/', 1)[1]
            elif '/agix/' in filepath:
                display_path = '/agix/' + filepath.split('/agix/', 1)[1]
            
            space_results.append({
                "name": space_name,
                "count": len(msgs),
                "path": display_path
            })
        except Exception as e:
            logger.warning(f"Failed to write file for space {space_name}: {e}")
            
    # Apply pagination to manifest
    total_spaces = len(space_results)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paged_results = space_results[start_idx:end_idx]
    
    total_pages = (total_spaces + page_size - 1) // page_size if total_spaces > 0 else 1
    
    manifest_lines = [
        f"### Google Chat Search Results (Page {page}/{total_pages})",
        f"Total messages found: {len(all_messages)} across {total_spaces} spaces.",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        ""
    ]
    
    if restricted:
        manifest_lines.append("⚠️ **Restricted Spaces** (Access Denied by API):")
        for res in restricted:
            manifest_lines.append(f"- {res}")
        manifest_lines.append("")

    if not paged_results and total_spaces > 0:
        manifest_lines.append(f"No results on page {page}. Total pages: {total_pages}")
    
    for res in paged_results:
        manifest_lines.append(f"- {res['name']}: {res['count']} messages → {res['path']}")
    
    if page < total_pages:
        manifest_lines.append(f"\n*Note: {total_spaces - end_idx} more spaces available. Call `google_chat_search_all_spaces` with `page={page+1}` to see more.*")
    
    return "\n".join(manifest_lines)

@mcp.tool()
def google_chat_find_user(name: str | List[str]) -> Dict[str, Any]:
    """Find a Google Chat user by name or email.
    
    Searches the local user map for matching users. Supports partial name
    matches or exact email matches. Use this to resolve specific user IDs.
    
    Args:
        name: Name string or list of name strings to search for.
    """
    _load_user_map_if_needed()
    results = {}
    
    names = [name] if isinstance(name, str) else name
    
    for n in names:
        n_lower = n.lower()
        matches = []
        for uid, info in _global_user_map.items():
            disp = info.get('displayName', '').lower()
            emails = [e.lower() for e in info.get('emails', [])]
            if n_lower in disp or any(n_lower == e for e in emails) or n_lower in uid.lower():
                matches.append({
                    "id": uid,
                    "displayName": info.get('displayName'),
                    "emails": info.get('emails', []),
                    "spaces": list(info.get('seenInSpaces', {}).keys())[:5] if isinstance(info.get('seenInSpaces'), dict) else []
                })
        results[n] = matches
    
    return results


@mcp.tool()
def google_chat_learn_users(space_ids: Optional[str] = None, scan_count: int = 50) -> str:
    """Scan recent messages to auto-learn user display names from @mention annotations.
    
    The Google Chat API doesn't return sender display names with user OAuth.
    This tool extracts names from @mention annotations in message text, building
    a persistent UID→name mapping file.
    
    Args:
        space_ids: Comma-separated space IDs to scan. If empty, scans all accessible spaces.
        scan_count: Number of messages to scan per space (default 50, max 200).
    
    Returns:
        Summary of discovered user mappings.
    """
    service = get_chat_service()
    scan_count = min(scan_count, 200)
    
    # Determine which spaces to scan
    spaces_to_scan = []
    if space_ids:
        spaces_to_scan = [s.strip() for s in space_ids.split(',')]
    else:
        try:
            result = service.spaces().list(pageSize=50).execute()
            for space in result.get('spaces', []):
                space_name = space.get('name', '')
                if space_name:
                    spaces_to_scan.append(space_name.replace('spaces/', ''))
        except Exception as e:
            return f"Failed to list spaces: {e}"
    
    discovered = {}  # user_id -> display_name
    evidence = {}    # user_id -> list of evidence sources
    
    for space_id in spaces_to_scan:
        space_id = space_id.replace('spaces/', '')
        try:
            results = service.spaces().messages().list(
                parent=f"spaces/{space_id}",
                pageSize=scan_count,
                orderBy="createTime desc"
            ).execute()
            
            for msg in results.get('messages', []):
                text = msg.get('text', '')
                annotations = msg.get('annotations', [])
                sender = msg.get('sender', {})
                
                # Check sender object for displayName (works for bots)
                sender_name = sender.get('name', '')
                sender_display = sender.get('displayName', '')
                if sender_name and sender_display and sender_name not in discovered:
                    discovered[sender_name] = sender_display
                    evidence.setdefault(sender_name, []).append(f"sender.displayName in space {space_id}")
                
                # Extract from @mention annotations
                for ann in annotations:
                    if ann.get('type') == 'USER_MENTION':
                        user_mention = ann.get('userMention', {})
                        mention_user = user_mention.get('user', {})
                        mention_user_name = mention_user.get('name', '')  # users/ID
                        mention_display = mention_user.get('displayName', '')
                        
                        # Try to get display name from the annotation's user object
                        if mention_user_name and mention_display and mention_user_name not in discovered:
                            discovered[mention_user_name] = mention_display
                            evidence.setdefault(mention_user_name, []).append(
                                f"annotation.userMention.displayName in space {space_id}"
                            )
                        
                        # Extract from message text at annotation position
                        if mention_user_name and not mention_display:
                            start = ann.get('startIndex', 0)
                            length = ann.get('length', 0)
                            if start >= 0 and length > 0 and start + length <= len(text):
                                mention_text = text[start:start + length].strip()
                                # Remove leading @ if present
                                if mention_text.startswith('@'):
                                    mention_text = mention_text[1:].strip()
                                if mention_text and len(mention_text) > 1 and mention_user_name not in discovered:
                                    discovered[mention_user_name] = mention_text
                                    evidence.setdefault(mention_user_name, []).append(
                                        f"@mention text '{mention_text}' in space {space_id}"
                                    )
        except Exception as e:
            logger.warning(f"Failed to scan space {space_id}: {e}")
            continue
    
    if not discovered:
        return "No user mappings discovered. Try scanning spaces with @mentions."
    
    # Load existing map, merge, and save
    data_dir = REGISTRY_FILE.rsplit('/', 1)[0] if '/' in REGISTRY_FILE else '/agix/data'
    user_map_file = os.path.join(data_dir, 'google_chat_users.json')
    
    existing = {}
    if os.path.exists(user_map_file):
        try:
            with open(user_map_file, 'r') as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    
    # Merge: don't overwrite existing confirmed mappings
    new_count = 0
    for uid, name in discovered.items():
        clean_uid = uid.replace('users/', '')
        full_uid = uid if uid.startswith('users/') else f'users/{uid}'
        if clean_uid not in existing and full_uid not in existing:
            existing[clean_uid] = name
            new_count += 1
            # Also update in-memory cache immediately
            _user_name_cache[full_uid] = name
            _global_user_map[full_uid] = {"displayName": name}
    
    # Save
    try:
        os.makedirs(os.path.dirname(user_map_file), exist_ok=True)
        with open(user_map_file, 'w') as f:
            json.dump(existing, f, indent=2)
        logger.info(f"Saved {len(existing)} user mappings to {user_map_file}")
    except Exception as e:
        logger.warning(f"Failed to save user map: {e}")
    
    # Format output
    lines = [f"### User Discovery Results"]
    lines.append(f"Scanned {len(spaces_to_scan)} spaces, {scan_count} messages each.")
    lines.append(f"**{new_count} new** mappings discovered ({len(existing)} total in map).\n")
    
    for uid, name in discovered.items():
        ev = evidence.get(uid, ['unknown'])
        lines.append(f"- `{uid}` → **{name}** (source: {ev[0]})")
    
    lines.append(f"\nMap saved to: `{user_map_file}`")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
