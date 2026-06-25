import asyncio
from typing import Any, Dict, Optional
from python.helpers.task_scheduler import TaskScheduler, TaskState

async def scheduler_reset_task(task_uuid: str, new_state: str = "ERROR", message: Optional[str] = None) -> str:
    """
    Forcibly reset the state of a task in the TaskScheduler. 
    Use this to clear stalled or stuck tasks that haven't hit the hard timeout yet.
    
    Args:
        task_uuid: The unique identifier of the task to reset.
        new_state: The state to move the task to (e.g. ERROR, IDLE, PENDING). Defaults to ERROR.
        message: Optional message to set as the task's last_result.
        
    Returns:
        A success message or error description.
    """
    scheduler = TaskScheduler.get()
    if not scheduler:
        return "Error: TaskScheduler not initialized."
        
    task = scheduler.get_task_by_uuid(task_uuid)
    if not task:
        return f"Error: Task with UUID {task_uuid} not found."
    
    # Map string state to TaskState enum
    try:
        target_state = TaskState(new_state.lower())
    except ValueError:
        return f"Error: Invalid state '{new_state}'. Valid states: {[s.value for s in TaskState]}"
        
    final_msg = message or f"Forced reset to {target_state.value} by supervisor tool."
    
    await scheduler.update_task(
        task_uuid,
        state=target_state,
        last_result=final_msg
    )
    
    return f"Successfully reset task '{task.name}' ({task_uuid}) to state: {target_state.value}"

if __name__ == "__main__":
    # Example CLI usage
    import sys
    if len(sys.argv) > 1:
        uuid = sys.argv[1]
        state = sys.argv[2] if len(sys.argv) > 2 else "ERROR"
        asyncio.run(scheduler_reset_task(uuid, state))
