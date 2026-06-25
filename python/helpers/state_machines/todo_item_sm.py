from python.helpers.agent_state_machine import AgentStateMachine

class TodoItemSM(AgentStateMachine):
    """Mirrors existing ALLOWED_TRANSITIONS in task_list.py."""
    WAL_ENABLED = True
    VALID_STATUSES = frozenset({"pending", "in_progress", "completed"})
    VALID_TRANSITIONS = {
        "pending": frozenset({"pending", "in_progress", "completed"}),
        "in_progress": frozenset({"in_progress", "completed"}),
        "completed": frozenset({"completed"}),
    }
    INITIAL_STATUS = "pending"
