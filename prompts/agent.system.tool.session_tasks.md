## session_tasks: Session Task List Management

Use this tool to manage the task/todo list for the current chat session. This helps track mission objectives, decompose complex tasks, and ensure all work items are completed.

### When to Use
- **At session start**: Create tasks from the user's mission/request
- **During work**: Track progress by starting/completing tasks
- **For complex tasks**: Decompose into subtasks with dependencies
- **For coordination**: Assign tasks to specific modes (code, architect, debug, etc.)

### Available Methods

#### list_tasks
List all tasks in the current session.
~~~json
{
    "thoughts": [
        "Let me check the current task list..."
    ],
    "headline": "Listing session tasks",
    "tool_name": "session_tasks:list_tasks",
    "tool_args": {
        "status": "pending",           // Optional: filter by status
        "assigned_to": "code",         // Optional: filter by assignee
        "format": "markdown"           // Optional: json, markdown, or summary
    }
}
~~~

#### add_task
Add a new task to the session.
~~~json
{
    "thoughts": [
        "I need to add a task for..."
    ],
    "headline": "Adding task to session",
    "tool_name": "session_tasks:add_task",
    "tool_args": {
        "description": "Implement user authentication",  // Required
        "priority": 2,                                   // Optional: 1-5 (1=critical, 5=optional)
        "assigned_to": "code",                           // Optional: mode/agent to assign
        "dependencies": ["task_id_1"],                   // Optional: tasks that must complete first
        "parent_id": "parent_task_id"                    // Optional: for subtasks
    }
}
~~~

#### start_task
Mark a task as in progress.
~~~json
{
    "thoughts": [
        "Starting work on this task..."
    ],
    "headline": "Starting task",
    "tool_name": "session_tasks:start_task",
    "tool_args": {
        "task_id": "abc123",           // Required
        "assigned_to": "code"          // Optional: who is working on it
    }
}
~~~

#### complete_task
Mark a task as completed.
~~~json
{
    "thoughts": [
        "Task completed successfully..."
    ],
    "headline": "Completing task",
    "tool_name": "session_tasks:complete_task",
    "tool_args": {
        "task_id": "abc123",           // Required
        "result": "Implemented login endpoint with JWT tokens"  // Optional: summary
    }
}
~~~

#### fail_task
Mark a task as failed.
~~~json
{
    "thoughts": [
        "This task failed due to..."
    ],
    "headline": "Marking task as failed",
    "tool_name": "session_tasks:fail_task",
    "tool_args": {
        "task_id": "abc123",           // Required
        "error": "Database connection failed"  // Optional: error message
    }
}
~~~

#### update_task
Update task properties.
~~~json
{
    "thoughts": [
        "Updating task properties..."
    ],
    "headline": "Updating task",
    "tool_name": "session_tasks:update_task",
    "tool_args": {
        "task_id": "abc123",           // Required
        "description": "New description",  // Optional
        "priority": 1,                     // Optional
        "assigned_to": "debug",            // Optional
        "status": "blocked"                // Optional: pending, in_progress, completed, blocked, failed, skipped
    }
}
~~~

#### remove_task
Remove a task from the list.
~~~json
{
    "thoughts": [
        "Removing this task..."
    ],
    "headline": "Removing task",
    "tool_name": "session_tasks:remove_task",
    "tool_args": {
        "task_id": "abc123"            // Required
    }
}
~~~

#### get_progress
Get progress statistics.
~~~json
{
    "thoughts": [
        "Checking overall progress..."
    ],
    "headline": "Getting task progress",
    "tool_name": "session_tasks:get_progress",
    "tool_args": {
        "format": "text"               // Optional: json or text
    }
}
~~~

#### get_next_task
Get the next actionable task by priority.
~~~json
{
    "thoughts": [
        "Finding the next task to work on..."
    ],
    "headline": "Getting next task",
    "tool_name": "session_tasks:get_next_task",
    "tool_args": {
        "assigned_to": "code"          // Optional: filter by assignee
    }
}
~~~

#### set_mission
Set the mission statement for the session.
~~~json
{
    "thoughts": [
        "Setting the mission for this session..."
    ],
    "headline": "Setting session mission",
    "tool_name": "session_tasks:set_mission",
    "tool_args": {
        "mission": "Build a REST API with authentication"  // Required
    }
}
~~~

### Task Status Values
- `pending` - Not yet started
- `in_progress` - Currently being worked on
- `completed` - Successfully finished
- `blocked` - Waiting on dependencies
- `failed` - Failed to complete
- `skipped` - Intentionally skipped

### Priority Levels
- `1` - Critical (must do first)
- `2` - High
- `3` - Medium (default)
- `4` - Low
- `5` - Optional

### Best Practices

1. **Start with a mission**: Use `set_mission` to define the overall goal
2. **Decompose complex tasks**: Break down into smaller, actionable items
3. **Use dependencies**: Link tasks that must complete in order
4. **Track progress**: Start tasks before working, complete when done
5. **Assign appropriately**: Use mode names (code, architect, debug, review, ask)
6. **Check progress regularly**: Use `get_progress` to see overall status

### Example Workflow

1. Set the mission:
~~~json
{
    "thoughts": ["Setting up the mission for this authentication project"],
    "headline": "Setting session mission",
    "tool_name": "session_tasks:set_mission",
    "tool_args": {
        "mission": "Build user authentication system"
    }
}
~~~

2. Add decomposed tasks:
~~~json
{
    "thoughts": ["Adding the first task - architecture design"],
    "headline": "Adding architecture task",
    "tool_name": "session_tasks:add_task",
    "tool_args": {
        "description": "Design auth architecture",
        "priority": 1,
        "assigned_to": "architect"
    }
}
~~~

3. Work through tasks:
~~~json
{
    "thoughts": ["Starting work on the design task"],
    "headline": "Starting task",
    "tool_name": "session_tasks:start_task",
    "tool_args": {
        "task_id": "abc123"
    }
}
~~~

... do the work ...

~~~json
{
    "thoughts": ["Design work is complete"],
    "headline": "Completing task",
    "tool_name": "session_tasks:complete_task",
    "tool_args": {
        "task_id": "abc123",
        "result": "Designed auth flow with JWT + refresh tokens"
    }
}
~~~

4. Check progress:
~~~json
{
    "thoughts": ["Checking how much progress we've made"],
    "headline": "Getting task progress",
    "tool_name": "session_tasks:get_progress",
    "tool_args": {}
}
~~~
