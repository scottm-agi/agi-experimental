from python.helpers.agent_state_machine import AgentStateMachine

class BuildLoopSM(AgentStateMachine):
    WAL_ENABLED = True
    VALID_STATUSES = frozenset({"ok", "tier1_warn", "tier2_escalate", "tier3_hard_block"})
    VALID_TRANSITIONS = {
        "ok": frozenset({"tier1_warn"}),
        "tier1_warn": frozenset({"tier2_escalate", "ok"}),
        "tier2_escalate": frozenset({"tier3_hard_block", "tier1_warn", "ok"}),
        "tier3_hard_block": frozenset({"tier2_escalate", "tier1_warn", "ok"}),
    }
    INITIAL_STATUS = "ok"
