# perplexity_ask

Engages in a conversation using the Perplexity Sonar API.

## Critical Instructions
- **Schema Enforcement**: You MUST use the `messages` argument, which is an array of message objects.
- **DO NOT** use a `query` argument.

### Argument Examples
```json
{
  "messages": [
    {
      "role": "user",
      "content": "Your search query here"
    }
  ]
}
```
