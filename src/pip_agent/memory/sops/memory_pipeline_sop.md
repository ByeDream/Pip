## L1 Reflection Rules

You are extracting behavioral observations from conversation transcripts.

### What to Record

- **Decision patterns** — How the user weighs trade-offs (e.g. "prefers simplicity over comprehensiveness", "optimizes for maintainability first").
- **Value systems** — What the user considers important (e.g. "values test coverage", "dislikes over-engineering").
- **Communication style** — How the user gives feedback, asks questions, or expresses dissatisfaction.
- **Cognitive heuristics** — Recurring mental shortcuts or reasoning frameworks (e.g. "always asks for the simplest solution first", "thinks in terms of data flow").
- **Quality standards** — Specific thresholds or criteria the user applies repeatedly.
- **Recurring preferences** — Consistent tool, language, framework, or pattern choices across sessions.
- **Workflow patterns** — How the user structures their work (e.g. "plans before coding", "iterates incrementally").

### What NOT to Record

- **One-time factual queries** — "What is the capital of France?" or "How do I use git rebase?" are not behavioral.
- **Specific code snippets or file paths** — These are ephemeral context, not behavioral patterns.
- **Task-specific details** — The content of what was built is not an observation; HOW it was approached is.
- **Emotional reactions to isolated events** — A single frustration is not a pattern; repeated frustration with the same type of issue is.
- **Information already stored in user profiles** — Names, timezones, identifiers belong in `users/*.md`, not observations.

### Observation Granularity

- Each observation should describe ONE atomic behavioral pattern.
- Prefer "User prefers X over Y when Z" format — specific and falsifiable.
- Avoid vague observations like "User is detail-oriented" — say what specific details they focus on.
- Include temporal context using absolute dates (derived from transcript timestamps).
- Write all observations in English regardless of conversation language.

## L2 Consolidation Rules

You are merging new observations into an existing memory store. Your goal is to maintain a compact, high-signal set of behavioral memories.

### REINFORCE

When a new observation semantically matches an existing memory:
- Increment `count` by 1.
- Update `last_reinforced` to the current epoch timestamp.
- Add the observation's `category` to `contexts` if not already present.
- Increment `total_cycles` by 1.
- Recalculate `stability` = len(unique contexts) / total_cycles (capped at 1.0).

Semantic matching criteria: two items match if they describe the same behavioral pattern, even if worded differently. "User prefers simple solutions" matches "User consistently chooses the minimal approach."

### CREATE

When an observation describes a genuinely novel pattern not covered by any existing memory:
- Create a new memory with: count=1, first_seen=current epoch, last_reinforced=current epoch, contexts=[observation category], total_cycles=1, stability=1.0, source="auto".
- Generate a unique `id` (12-char hex).

Do NOT create a new memory if an existing one already covers the same behavioral pattern — reinforce instead.

### DECAY

For every existing memory NOT reinforced by any observation in this cycle:
- Decrement `count` by 1.
- Increment `total_cycles` by 1.
- Recalculate `stability`.

All memories decay uniformly. There are no exemptions.

### FORGET

- Remove any memory whose `count` has dropped to 0 or below.
- This is the natural lifecycle: patterns that stop appearing in conversations fade away.

### CONFLICT Resolution

When two memories contradict each other (e.g. "User prefers tabs" vs "User prefers spaces"):
- The memory with the higher `count` wins — it has more evidence supporting it.
- If `count` is equal, the memory with the more recent `last_reinforced` wins — newer information takes precedence.
- The losing memory is REMOVED entirely.
- The winning memory inherits the loser's `contexts` (merge, deduplicate) and adds the loser's `total_cycles` to its own.

When an observation contradicts an existing memory:
- If the observation represents a clear change in behavior, create a new memory (count=1) and let natural decay handle the old one.
- If the observation simply updates an existing pattern, reinforce the existing memory with updated text.

### Stability Formula

`stability = len(set(contexts)) / total_cycles`

Stability measures how consistently a pattern appears across different types of interactions. High stability (close to 1.0) means the pattern shows up in many different contexts, not just one.

## L3 Axiom Distillation Rules

You are distilling high-confidence behavioral memories into judgment principles (axioms).

### Promotion Criteria

Only memories meeting ALL of the following qualify:
- `count` >= 5 (reinforced at least 5 times).
- `stability` >= 0.5 (appears across multiple contexts).

### Axiom Standards

- Each axiom describes HOW the user thinks or decides, not WHO they are.
- Focus on decision heuristics, quality standards, and cognitive patterns.
- Each axiom is 1-2 sentences, precise and actionable.
- Axioms should be useful for an AI assistant to adjust its behavior.
- Maximum 20 axioms. If more qualify, keep only the highest-count ones.

### Output Format

Output as a markdown bullet list. Each item is one principle.

## Global Constraints

- **MAX_MEMORIES**: 200. If the memory list exceeds this, keep only the top 200 by `count` (descending).
- **Language**: All memory text and axioms must be written in English, regardless of the conversation language.
- **Output format**: Return ONLY the requested format (JSON array for L2, markdown list for L3). No markdown fences, no extra commentary.
- **ID preservation**: When reinforcing or decaying existing memories, preserve their original `id`. Only generate new IDs for newly created memories.
