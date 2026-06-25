# Data Flow Completeness

## Problem
When a feature generates or computes data (e.g., AI-generated content, computed scores), the database schema may not have a field to store it. The data gets computed at runtime but is silently discarded.

## Wrong (common mistake)
```typescript
// API generates AI email content
const emailContent = await generateAGIX(prompt);
// But the Outreach model has no 'content' field!
await prisma.outreach.create({
  data: {
    prospectId: prospect.id,
    // content: emailContent  ← field doesn't exist in schema!
  }
});
```

## Correct Pattern — Schema-First Design
```typescript
// 1. FIRST: Add the field to the schema
// prisma/schema.prisma:
// model Outreach {
//   id        String @id
//   content   String  // ← ADD THIS
//   subject   String  // ← AND THIS
//   ...
// }

// 2. THEN: Use it in the API
const emailContent = await generateAGIX(prompt);
await prisma.outreach.create({
  data: {
    prospectId: prospect.id,
    content: emailContent,
    subject: emailSubject,
  }
});
```

## Rules
1. Before implementing any data pipeline, trace the FULL flow: Source → Transform → Store → Display
2. Every computed or generated value MUST have a corresponding storage field in the schema
3. Never compute data that gets thrown away — if it's worth computing, it's worth persisting
4. After creating any API endpoint that produces data, verify the database schema has fields for ALL outputs
5. Run `prisma db push` or equivalent migration after schema changes

## When This Appears
- AI-generated content (emails, reports, summaries) with no storage field
- Computed scores or rankings that exist only in memory
- Webhook payloads that are processed but not recorded
- Any feature where "generate" and "persist" are in different code paths
