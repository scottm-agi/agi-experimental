from python.helpers.agent_state_machine import AgentStateMachine

class GoalSM(AgentStateMachine):
    WAL_ENABLED = True
    VALID_STATUSES = frozenset({"pending", "in_progress", "blocked", "completed", "verified", "failed"})
    VALID_TRANSITIONS = {
        "pending": frozenset({"in_progress"}),
        "in_progress": frozenset({"blocked", "completed", "failed"}),
        "blocked": frozenset({"in_progress", "failed"}),
        "completed": frozenset({"verified"}),
        "verified": frozenset(),
        "failed": frozenset({"pending"}),
    }
    INITIAL_STATUS = "pending"

class SubgoalSM(AgentStateMachine):
    WAL_ENABLED = True
    VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "skipped"})
    VALID_TRANSITIONS = {
        "pending": frozenset({"in_progress", "skipped"}),
        "in_progress": frozenset({"completed", "skipped"}),
        "completed": frozenset(),
        "skipped": frozenset(),
    }
    INITIAL_STATUS = "pending"
