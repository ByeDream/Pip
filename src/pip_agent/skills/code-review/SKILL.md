---
name: code-review
description: >-
  Perform thorough code reviews with security, performance, and maintainability
  analysis. Use when the user asks to review code, check for bugs, or audit
  a codebase.
tags: [review, security, quality]
---

# Code Review

Follow this structured approach when reviewing code.

## Review Checklist

### 1. Security (Critical)

- [ ] **Injection**: SQL, command, XSS, template injection
- [ ] **Auth**: Hardcoded credentials, missing access controls
- [ ] **Data exposure**: Sensitive data in logs or error messages
- [ ] **Dependencies**: Known vulnerabilities (`pip-audit`, `npm audit`)

### 2. Correctness

- [ ] **Logic errors**: Off-by-one, null handling, edge cases
- [ ] **Race conditions**: Concurrent access without synchronization
- [ ] **Resource leaks**: Unclosed files, connections, memory
- [ ] **Error handling**: Swallowed exceptions, missing error paths

### 3. Performance

- [ ] **N+1 queries**: Database calls in loops
- [ ] **Blocking I/O**: Sync operations in async code
- [ ] **Inefficient algorithms**: O(n^2) when O(n) is possible
- [ ] **Missing caching**: Repeated expensive computations

### 4. Maintainability

- [ ] **Naming**: Clear, consistent, descriptive
- [ ] **Complexity**: Functions > 50 lines, nesting > 3 levels
- [ ] **Duplication**: Copy-pasted code blocks
- [ ] **Dead code**: Unused imports, unreachable branches

### 5. Testing

- [ ] **Coverage**: Critical paths tested
- [ ] **Edge cases**: Null, empty, boundary values
- [ ] **Assertions**: Meaningful, specific checks

## Common Patterns to Flag

### Python

```python
# Bad: SQL injection
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
# Good:
cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))

# Bad: Command injection
os.system(f"ls {user_input}")
# Good:
subprocess.run(["ls", user_input], check=True)

# Bad: Mutable default argument
def append(item, lst=[]):
# Good:
def append(item, lst=None):
    lst = lst or []
```

## Output Format

```markdown
## Code Review: [file/component]

### Summary
[1-2 sentence overview]

### Critical Issues
1. **[Issue]** (line X): [Description]
   - Impact: [What could go wrong]
   - Fix: [Suggested solution]

### Improvements
1. **[Suggestion]** (line X): [Description]

### Positive Notes
- [What was done well]

### Verdict
[ ] Ready to merge
[ ] Needs minor changes
[ ] Needs major revision
```

## Workflow

1. **Understand context**: Read PR description, linked issues
2. **Run the code**: Build, test, run locally if possible
3. **Read top-down**: Start with main entry points
4. **Check tests**: Are changes tested? Do tests pass?
5. **Security scan**: Run automated tools
6. **Manual review**: Use checklist above
7. **Write feedback**: Be specific, suggest fixes, be kind
