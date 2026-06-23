# Work Queue

Items move through: **idea → ready → in-progress → done**

- **idea** — added by Ross, not yet discussed or refined
- **ready** — discussed, acceptance criteria agreed, safe to implement autonomously
- **in-progress** — Claude is currently working on it
- **done** — implemented; moved to archive below

Add new items at the top of the relevant section. Use the template below as a guide — rough is fine for `idea`, but `ready` items need the full template filled in.

---

## Template

```
### [Title]
**Status:** idea | ready | in-progress | done
**Added:** YYYY-MM-DD

**Description:**
What you want and why.

**Acceptance criteria:**
- [ ] ...

**Notes / constraints:**
Any gotchas, design decisions made during discussion, or things to watch out for.
```

---

## Ideas

<!-- Add new ideas here -->

---

## Ready

<!-- Items that have been discussed and are ready to implement autonomously -->

---

## In Progress

<!-- Claude is currently working on these -->

---

## Done

### Recurring task delete modal
**Status:** done
**Added:** 2026-06-23

**Description:**
When deleting a recurring/variable_recurring/workout task from the daily view, show a modal with two choices: remove just today's projection row (task persists and continues on future days), or delete the entire task entity (current behaviour). Both actions produce an undo toast.
