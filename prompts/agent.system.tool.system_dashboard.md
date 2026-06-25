---
name: System Dashboard
description: Tool for fetching system performance metrics, token usage, and analytics for the A2UI dashboard.
---

# `system_dashboard`

Executes queries to gather global system metrics and returns pre-formatted A2UI components for the Dashboard Agent.

## Arguments
- `action` (string, optional): The type of dashboard to generate (e.g. "full_dashboard", "token_usage"). Default is "full_dashboard".

## Usage
Simply call the tool without arguments to get the full dashboard payload, and then echo the provided A2UI code block to the user.
