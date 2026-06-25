"""
Task Heartbeat Extension
Publishes periodic heartbeat signals for enhanced-supervision tasks.
Part of Supervisor Reliability Enhancement - Gap 4.
Priority: 20 (runs after goal tracking)
"""

from python.helpers.extension import Extension
from python.helpers.log import Log
import logging
import time


class TaskHeartbeatExtension(Extension):
    """Extension to publish heartbeats for enhanced supervision tasks."""
    
    # Class-level storage for heartbeat timestamps
    _last_heartbeat: dict = {}
    HEARTBEAT_INTERVAL = 60  # seconds
    
    async def execute(self, loop_data=None, **kwargs):
        """Execute heartbeat check on each message loop iteration."""
        agent = self.agent
        context = agent.context
        
        # Import here to avoid circular imports
        try:
            from python.helpers.task_definitions import (
                get_task_supervision_level, 
                SupervisionLevel,
                HEARTBEAT_INTERVAL
            )
            from python.helpers.event_bus import get_event_bus, AgentSignal, SignalType
            from python.agent import AgentContextType
        except ImportError as e:
            Log.debug(f"[TaskHeartbeat] Import error: {e}")
            return loop_data
        
        # Only for TASK context agents
        context_type = getattr(context, 'type', None)
        if context_type is None:
            return loop_data
            
        try:
            if context_type != AgentContextType.TASK:
                return loop_data
        except Exception as e:
            # If comparison fails, log and skip
            logging.getLogger(__name__).warning(f"[HEARTBEAT] Task heartbeat context comparison failed: {e}")
            return loop_data
        
        # Determine task name from context or agent
        task_name = getattr(context, 'task_name', '') or getattr(context, 'name', '') or str(agent.number)
        
        # Only for enhanced supervision tasks
        supervision_level = get_task_supervision_level(task_name)
        if supervision_level != SupervisionLevel.ENHANCED:
            return loop_data
        
        # Check if heartbeat is due
        now = time.time()
        agent_key = f"{agent.number}_{context.id}"
        last = self._last_heartbeat.get(agent_key, 0)
        
        if now - last >= HEARTBEAT_INTERVAL:
            # Publish heartbeat signal
            try:
                # Check if HEARTBEAT_OK exists in SignalType
                if hasattr(SignalType, 'HEARTBEAT_OK'):
                    signal_type = SignalType.HEARTBEAT_OK
                else:
                    # Fallback to a generic info signal
                    signal_type = SignalType.AGENT_INFO if hasattr(SignalType, 'AGENT_INFO') else None
                    if not signal_type:
                        Log.debug("[TaskHeartbeat] No suitable signal type for heartbeat")
                        self._last_heartbeat[agent_key] = now
                        return loop_data
                
                signal = AgentSignal(
                    signal_type=signal_type,
                    agent_id=str(agent.number),
                    context_id=context.id,
                    severity="info",
                    error_message=f"Heartbeat OK - iteration {getattr(loop_data, 'iteration', 0) if loop_data else 0}, task: {task_name}",
                    timestamp=now
                )
                
                # Add context_type if supported
                if hasattr(signal, 'context_type'):
                    signal.context_type = "TASK"
                
                event_bus = get_event_bus()
                await event_bus.publish(signal)
                
                Log.debug(f"[TaskHeartbeat] Published heartbeat for {task_name}")
                
            except Exception as e:
                Log.debug(f"[TaskHeartbeat] Failed to publish heartbeat: {e}")
            
            self._last_heartbeat[agent_key] = now
        
        return loop_data
