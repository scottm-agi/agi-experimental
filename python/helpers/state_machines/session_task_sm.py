from python.helpers.agent_state_machine import AgentStateMachine

class SessionTaskSM(AgentStateMachine):
    WAL_ENABLED = True
    VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "blocked", "failed", "skipped"})
    VALID_TRANSITIONS = {
        "pending": frozenset({"in_progress", "skipped"}),
        "in_progress": frozenset({"completed", "blocked", "failed"}),
        "blocked": frozenset({"pending", "in_progress"}),
        "completed": frozenset(),
        "failed": frozenset(),
        "skipped": frozenset(),
    }
    INITIAL_STATUS = "pending"
