# Workflow Rules

## Rule 1: Execute directly
Codex may run commands, inspect files, edit files, and perform other local project operations needed to complete the requested task without asking for permission first.

## Rule 2: Two-part response format
Always give a two-part result:
1. **Detailed explanation** - full reasoning, approach, and solution details
2. **Summary** - a concise recap of the key points

## Rule 3: Commit after changes
After making project changes, commit the tracked changes with a clear commit message that explains what changed and why. Do not include generated runtime data, local databases, secrets, or unrelated user files in the commit.
