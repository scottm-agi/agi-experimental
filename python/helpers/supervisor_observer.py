"""
Supervisor Observer — Parallel Agent Monitoring (Phase 5)

Provides the intelligent supervisor (Layer 2) with visibility into
parallel subordinate agents spawned by fan_out_subordinates.py.

Design principles:
  • Decoupled observer — does NOT stream duplicate tokens
  • Reads agent state snapshots (counters, errors, fingerprints)
  • Aggregates health across all running subordinates
  • Exposes intervention hooks: pause, redirect, kill individual agents

Usage:
    from python.helpers.supervisor_observer import SupervisorObserver
    
    observer = SupervisorObserver(parent_agent)
    health = observer.get_pool_health()
    stuck = observer.find_stuck_agents()
    observer.inject_guidance(agent_id, "Try a different approach")
"""

from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from python.agent import Agent

logger = logging.getLogger("agix.supervisor_observer")


@dataclass
class AgentHealthSnapshot:
    """Lightweight health snapshot of a single subordinate agent."""
    agent_name: str
    agent_number: int
    absolute_turns: int
    error_count: int
    fingerprint_repeats: int  # how many MD5 duplicates
    is_stuck: bool
    last_tool: Optional[str] = None
    elapsed_seconds: float = 0.0
    status: str = "running"  # running | stuck | completed | failed

    @property
    def health_score(self) -> float:
        """0.0 (dead) to 1.0 (healthy). Used for pool-level aggregation."""
        score = 1.0
        if self.error_count > 0:
            score -= min(self.error_count * 0.15, 0.6)
        if self.fingerprint_repeats > 2:
            score -= 0.3
        if self.is_stuck:
            score -= 0.4
        return max(0.0, score)


@dataclass 
class PoolHealth:
    """Aggregated health of all parallel subordinates."""
    total_agents: int = 0
    running: int = 0
    stuck: int = 0
    completed: int = 0
    failed: int = 0
    avg_health_score: float = 1.0
    stuck_agents: List[AgentHealthSnapshot] = field(default_factory=list)
    
    @property
    def needs_intervention(self) -> bool:
        """True if pool-level intervention is warranted."""
        if self.total_agents == 0:
            return False
        # More than 30% stuck or average health below 0.4
        stuck_ratio = self.stuck / self.total_agents if self.total_agents > 0 else 0
        return stuck_ratio > 0.3 or self.avg_health_score < 0.4


class SupervisorObserver:
    """
    Observer for parallel agent pools.
    
    Reads agent state WITHOUT streaming tokens or duplicating messages.
    Uses the agent's self-sanity counters (L0) to assess health.
    """
    
    def __init__(self, parent_agent: "Agent"):
        self.parent = parent_agent
    
    def _get_subordinate_pool(self) -> List["Agent"]:
        """Get all active subordinate agents from the parent."""
        pool = []
        
        # Check batch agent pool (fan_out_subordinates)
        batch_pool = self.parent.data.get("_batch_agent_pool", [])
        if isinstance(batch_pool, list):
            pool.extend(batch_pool)
        
        # Check single subordinate
        sub = self.parent.data.get("_subordinate")
        if sub:
            if isinstance(sub, list):
                pool.extend(sub)
            else:
                pool.append(sub)
        
        return pool
    
    def snapshot_agent(self, agent: "Agent") -> AgentHealthSnapshot:
        """Create a health snapshot from an agent's self-sanity state."""
        abs_turns = getattr(agent, "_absolute_turns", 0)
        error_count = getattr(agent, "_error_count", 0)
        
        # Count fingerprint repeats from action_fingerprints
        fingerprints = getattr(agent, "_action_fingerprints", [])
        seen = {}
        max_repeats = 0
        for fp in fingerprints:
            seen[fp] = seen.get(fp, 0) + 1
            max_repeats = max(max_repeats, seen[fp])
        
        # Determine if stuck via L1 escalation signals
        l2_signals = agent.data.get("_l2_escalation_signals", [])
        is_stuck = len(l2_signals) > 0
        
        # Get last tool used
        recent_tools = agent.data.get("recent_tool_calls", [])
        last_tool = None
        if recent_tools:
            last_entry = recent_tools[-1]
            if isinstance(last_entry, dict):
                last_tool = last_entry.get("name", last_entry.get("tool_name"))
        
        # Calculate elapsed time
        start_time = agent.data.get("_start_time", time.time())
        elapsed = time.time() - start_time
        
        # Determine status
        status = "running"
        if is_stuck:
            status = "stuck"
        elif error_count >= getattr(agent, "_error_budget", 10):
            status = "failed"
        
        return AgentHealthSnapshot(
            agent_name=agent.agent_name,
            agent_number=agent.number,
            absolute_turns=abs_turns,
            error_count=error_count,
            fingerprint_repeats=max_repeats,
            is_stuck=is_stuck,
            last_tool=last_tool,
            elapsed_seconds=elapsed,
            status=status,
        )
    
    def get_pool_health(self) -> PoolHealth:
        """Aggregate health across all subordinates."""
        pool = self._get_subordinate_pool()
        
        if not pool:
            return PoolHealth()
        
        snapshots = [self.snapshot_agent(a) for a in pool]
        
        running = [s for s in snapshots if s.status == "running"]
        stuck = [s for s in snapshots if s.status == "stuck"]
        completed = [s for s in snapshots if s.status == "completed"]
        failed = [s for s in snapshots if s.status == "failed"]
        
        scores = [s.health_score for s in snapshots]
        avg_score = sum(scores) / len(scores) if scores else 1.0
        
        return PoolHealth(
            total_agents=len(snapshots),
            running=len(running),
            stuck=len(stuck),
            completed=len(completed),
            failed=len(failed),
            avg_health_score=avg_score,
            stuck_agents=stuck,
        )
    
    def find_stuck_agents(self) -> List[AgentHealthSnapshot]:
        """Return snapshots of agents that appear stuck."""
        pool = self._get_subordinate_pool()
        snapshots = [self.snapshot_agent(a) for a in pool]
        return [s for s in snapshots if s.is_stuck or s.health_score < 0.4]
    
    async def inject_guidance(self, agent: "Agent", message: str) -> bool:
        """Inject a corrective guidance message into a subordinate's history.
        
        This is how L2 redirects a stuck parallel agent without
        streaming duplicate tokens — it adds a system warning.
        """
        try:
            await agent.hist_add_warning(message=message)
            logger.info(
                f"[SUPERVISOR OBSERVER] Injected guidance into "
                f"{agent.agent_name}: {message[:80]}..."
            )
            return True
        except Exception as e:
            logger.error(f"Failed to inject guidance into {agent.agent_name}: {e}")
            return False
    
    def kill_agent(self, agent: "Agent") -> bool:
        """Force-stop a subordinate by setting its turn counter to max.
        
        This triggers the L1 hard stop on the agent's next turn.
        """
        try:
            max_turns = agent.data.get("maxTurns", 50)
            agent._absolute_turns = max_turns + 1
            logger.warning(
                f"[SUPERVISOR OBSERVER] Killed {agent.agent_name} — "
                f"set _absolute_turns={agent._absolute_turns}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to kill {agent.agent_name}: {e}")
            return False
    
    def format_pool_summary(self) -> str:
        """Format a human-readable pool health summary for L2 context."""
        health = self.get_pool_health()
        
        if health.total_agents == 0:
            return "No parallel subordinates active."
        
        lines = [
            f"Pool Health: {health.total_agents} agents "
            f"({health.running} running, {health.stuck} stuck, "
            f"{health.completed} completed, {health.failed} failed)",
            f"Average health score: {health.avg_health_score:.2f}",
        ]
        
        if health.stuck_agents:
            lines.append("Stuck agents:")
            for s in health.stuck_agents:
                lines.append(
                    f"  • {s.agent_name}: {s.absolute_turns} turns, "
                    f"{s.error_count} errors, last_tool={s.last_tool}"
                )
        
        if health.needs_intervention:
            lines.append("⚠️ POOL INTERVENTION NEEDED")
        
        return "\n".join(lines)
