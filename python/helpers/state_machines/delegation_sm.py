from python.helpers.agent_state_machine import AgentStateMachine

class DelegationSM(AgentStateMachine):
    WAL_ENABLED = True
    VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "failed", "partial"})
    VALID_TRANSITIONS = {
        "pending": frozenset({"in_progress"}),
        "in_progress": frozenset({"completed", "failed", "partial"}),
        "completed": frozenset(),
        "failed": frozenset({"pending"}),
        "partial": frozenset(),
    }
    INITIAL_STATUS = "pending"
