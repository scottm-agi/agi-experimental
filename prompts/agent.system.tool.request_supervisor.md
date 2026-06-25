## request_supervisor: Request Supervisor Guidance

Use this tool when you need help, are stuck, or want guidance from the supervisor.

**When to use this tool:**
- You're unsure how to proceed with a task
- You've tried multiple approaches without success
- You need guidance on a complex decision
- You want a second opinion on your approach
- You're encountering repeated errors you can't resolve
- The task seems beyond your current capabilities

**Arguments:**
- **reason** (required): Why you're requesting help. Be specific about what's blocking you.
- **context** (optional): Additional context about the situation that might help the supervisor understand.
- **question** (optional): A specific question you want answered.
- **approaches_tried** (optional): List of approaches you've already attempted.

**Example usage:**

~~~json
{
    "thoughts": [
        "I've tried three different approaches to parse this XML file but keep getting errors.",
        "The file structure seems unusual and I'm not sure how to handle it.",
        "I should request supervisor guidance to get a fresh perspective."
    ],
    "tool_name": "request_supervisor",
    "tool_args": {
        "reason": "Unable to parse XML file despite multiple attempts",
        "context": "The XML file has nested namespaces and unusual encoding",
        "question": "What's the best approach for parsing XML with complex namespaces?",
        "approaches_tried": [
            "Used ElementTree with default parser - failed on namespace",
            "Tried lxml with namespace mapping - encoding error",
            "Attempted to strip namespaces first - lost important data"
        ]
    }
}
~~~

**What happens:**
1. Your request is sent to the supervisor LLM
2. The supervisor analyzes your full chat context
3. The supervisor provides guidance, hints, or redirects your approach
4. You receive the guidance and can continue with the task

**Note:** The supervisor is here to help, not judge. Don't hesitate to ask for help when you need it. It's better to ask early than to waste time on approaches that won't work.
