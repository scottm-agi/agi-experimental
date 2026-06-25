## Tool: view_skill

Load the full instructions of a skill by name. Use this tool to access detailed skill workflows when you need them.

### Usage

~~~json
{
    "tool_name": "view_skill",
    "tool_args": {
        "skill_name": "fullstack-conventions"
    }
}
~~~

### Parameters
- **skill_name** (required): The name of the skill to load (as shown in the skills index in your system prompt)

### When to Use
- **Before starting a specialized workflow**: When the skills index in your system prompt lists a skill relevant to your current task, load it to get the full instructions
- **When delegated a task that matches a skill**: Check the skills index and load matching skills before beginning work
- **When you need best practices**: Skills encode proven workflows and conventions for common operations

### What You'll Get
The tool returns the full SKILL.md content including:
- Detailed step-by-step instructions
- Best practices and conventions
- Configuration requirements
- Common pitfalls and solutions

### Important
- Only load skills that are relevant to your current task — don't load all skills at once
- The skills index in your system prompt shows available skills with brief descriptions
- If a skill is not found, the tool will list available skills to help you find the right one
