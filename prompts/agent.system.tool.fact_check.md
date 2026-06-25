## fact_check

Use the `fact_check` tool to **verify claims and ground facts** before presenting them to the user.

This tool ensures agents use up-to-date, verified information by adding temporal context and search-based verification.

### When to Use
- Before stating statistics, dates, or version numbers
- When generating technical documentation
- When citing external tools, APIs, or services
- When the user asks about current events or recent changes
- Before making claims about pricing, availability, or compatibility

### Arguments
- **claim**: The fact, claim, or data point to verify (required)
- **context**: Additional context about where/how this claim will be used
- **type**: Verification type:
  - `general` — Standard fact check
  - `temporal` — Time-sensitive information (e.g., "latest version of X")
  - `technical` — Technical accuracy (e.g., API docs, library features)
  - `data` — Statistics and data points

### Verification Output
The tool returns a structured verification report:
1. **Verdict**: Confirmed / Partially True / Unverified / False
2. **Evidence**: Key sources and data points
3. **Temporal note**: Whether information is time-sensitive
4. **Confidence**: High / Medium / Low

### Example
~~~json
{
    "claim": "Python 3.12 supports pattern matching with guards",
    "type": "technical",
    "context": "Writing a tutorial about Python features"
}
~~~
