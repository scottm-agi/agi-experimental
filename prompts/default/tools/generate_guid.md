## generate_guid
Generate stable, content-addressable requirement GUIDs for traceability.

**When to use this tool:**
- During Phase 0 planning: assign a GUID to each extracted requirement
- During task decomposition: assign a GUID to each subtask you create
- When creating delegation tasks: attach requirement GUIDs to link tasks to requirements
- Anytime you need a stable, reproducible identifier for a piece of text

**GUID format:** `REQ-{8-char MD5 hash}` — deterministic, case-insensitive, whitespace-normalized.

### Parameters:
- **text** (string): Single requirement text → returns one GUID
- **texts** (array of strings): Batch of requirements → returns array of `{text, id}` pairs

### Single Mode:
```json
{"text": "Booking link at https://booking.example.com/team"}
```
Returns: `REQ-a1b2c3d4`

### Batch Mode:
```json
{"texts": ["Booking integration link", "Payment gateway integration", "3-email drip campaign"]}
```
Returns:
```json
[
  {"text": "Booking integration link", "id": "REQ-a1b2c3d4"},
  {"text": "Payment gateway integration", "id": "REQ-e5f6a7b8"},
  {"text": "3-email drip campaign", "id": "REQ-c9d0e1f2"}
]
```

### Notes:
- Same text always produces the same GUID (content-addressable)
- Case and whitespace are normalized before hashing
- Use batch mode when processing the full user prompt decomposition
- Attach GUIDs to every `call_subordinate` delegation via the `requirement_ids` field
