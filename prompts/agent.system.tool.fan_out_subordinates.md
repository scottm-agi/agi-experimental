# fan_out_subordinates

Launch multiple subordinate agents in parallel for concurrent task execution.
Uses Redis Streams for durable task dispatch and result aggregation, with
asyncio.gather for in-process parallelism. Scales to 50 agents by default
(configurable via parallel_config.yaml).

## When to use
- Reading many issues/documents in parallel (e.g., 50 git issues)
- Scraping multiple websites concurrently (10+ sites)
- Fan-out research across multiple topics
- Per-space crawling (Google Chat spaces, Slack channels)
- Any workload that benefits from concurrent subordinate execution

## Architecture
1. **Redis Streams** dispatch tasks to `swarm:fanout:tasks:<job_id>`
2. **asyncio.Semaphore** bounds concurrent agents to `max_concurrent`
3. **asyncio.gather()** runs agent monologues in parallel
4. **Redis Streams** collect results to `swarm:fanout:results:<job_id>`
5. **Redis Hash** tracks progress at `swarm:fanout:progress:<job_id>`
6. Falls back to pure asyncio if Redis is unavailable

## Parameters
- **tasks** (required): List of task objects. Each task has:
  - `message` (string): The task description for the subordinate
  - `mode` (string, optional): Agent mode (code, architect, ask, debug, review)
  - `profile` (string, optional): Agent profile to use
- **max_concurrent** (optional, default from config/50): Maximum simultaneous agents

## Example usage

```json
{
  "tool_name": "fan_out_subordinates",
  "tool_args": {
    "tasks": [
      {"message": "Read and summarize issue #1"},
      {"message": "Read and summarize issue #2"},
      {"message": "Read and summarize issue #3"},
      {"message": "Search for information about X", "mode": "ask"},
      {"message": "Review code in module Y", "mode": "review"}
    ],
    "max_concurrent": 10
  }
}
```

## Scaling examples

### Read 50 git issues in parallel
```json
{
  "tool_name": "fan_out_subordinates",
  "tool_args": {
    "tasks": [
      {"message": "Read issue #1 and summarize key points"},
      {"message": "Read issue #2 and summarize key points"},
      ...
    ],
    "max_concurrent": 50
  }
}
```

### Multi-source data gathering
```json
{
  "tool_name": "fan_out_subordinates",
  "tool_args": {
    "tasks": [
      {"message": "Scrape https://example.com for pricing data"},
      {"message": "Query the Forgejo API for open PRs"},
      {"message": "Search Tavily for competitor analysis"},
      {"message": "Read the project README and extract architecture"}
    ],
    "max_concurrent": 10
  }
}
```

## Notes
- Results are collected and returned together to the parent in original order
- Rate limiting is coordinated across all subordinates via distributed RateLimiter
- Tasks are bounded by asyncio.Semaphore (not batched sequentially)
- Each subordinate has its own iteration budget
- Tool data anchors propagate back to the parent for fidelity verification
- Progress is tracked in Redis hash with 1h TTL
- Failed tasks return error strings without blocking other tasks
- Max concurrent capped at 50 (configurable via parallel_config.yaml)
