from typing import Any, Optional
from python.helpers.redis_history import get_redis_history_helper
from python.helpers.print_style import PrintStyle

async def fetch_large_result(redis_key: str) -> str:
    """
    Retrieve the full content of a summarized tool output from Redis.
    Use this when the summary isn't sufficient for your next step.
    
    Args:
        redis_key (str): The Redis key for the large result (e.g., 'chat:large_result:session_id:message_id').
        
    Returns:
        str: The full tool result content.
    """
    if not redis_key:
        return "Error: No redis_key provided."
        
    try:
        # Parse redis_key to get session_id and message_id
        # Format: chat:large_result:{session_id}:{message_id}
        parts = redis_key.split(':')
        if len(parts) < 4:
            return f"Error: Invalid redis_key format: {redis_key}"
            
        session_id = parts[2]
        message_id = parts[3]
        
        redis_helper = get_redis_history_helper()
        content = await redis_helper.get_large_result(session_id, message_id)
        
        if content is None:
            return f"Error: Full result not found in Redis for key {redis_key}. It may have expired."
            
        PrintStyle(font_color="green").print(f"Successfully retrieved large result ({len(str(content))} bytes) from Redis.")
        return str(content)
        
    except Exception as e:
        error_msg = f"Error retrieving large result from Redis: {e}"
        PrintStyle(font_color="red").print(error_msg)
        return error_msg
