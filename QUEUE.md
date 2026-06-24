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

- If i create a task with the same title as another task that already exists, the ui should notify me to avoid accidentally creating duplicate tasks. There are some tasks (for example, work) that could happily have more than one instance with the same name, but its always helpful to be made aware if there is already a task with that name.

- it might be good to have an auto-generated task for deadlines, so if, say 75% of the time has passed since the deadline was created and it still hasn't been done, a task will be added to my day to remind me and give me time to plan out the task in the deadline (eg plan how to accomplish the task, book equipment or appointment etc, buy materials needed), so if the deadline ends up getting close and i haven't yet done the thing, at least i will be ready to do it and not find myself with no time to prepare.

- I want to be able to tag tasks as a way of adding auto-generated tasks to them. For example, if i have a job interview or a wedding or some task where I need to be presentable, I could tag those tasks as '#presentable' or whatever, and that would auto-generate preset tasks to get a haircut and prepare my outfit a few days prior. or if i'm visiting family or doing any long car journey, there should be a 'travelling' tag that automatically sets a reminder to fill the car up with petrol and check the oil and tyres. tags should be suggested every time a new task is created, maybe even a small language model could handle the suggestions (even if i have the app running on a 4gb raspberry pi, vibe-thinker 3b might be small and powerful enough for that, but i will soon be running it on the main pc anyway hopefully), but if i can't get intelligent suggestions working, the app should still remind the user to tag newly created tasks

---

## Ready

### Task tagging and categorisation
**Status:** ready
**Added:** 2026-06-23

**Description:**
Add a tag system so tasks can be visually categorised. Tags are pre-defined (name + icon + color) and managed in the admin panel. Tasks can have any number of tags. In the daily view, tagged tasks display a colored left border stripe and small Lucide icon(s) so related tasks are immediately recognisable at a glance. No changes to the scheduling algorithm.

**Acceptance criteria:**
- [ ] `tags` table: `id`, `name`, `icon` (Lucide icon name string), `color` (hex from approved palette)
- [ ] `task_tags` join table: `task_id`, `tag_id`, cascade delete on either side
- [ ] Admin panel: tag list page showing all tags with their icon and color swatch
- [ ] Admin panel: create/edit tag form — name field, color picker (8 swatches, radio buttons), icon field (text input for Lucide icon name with a live preview rendering the icon)
- [ ] Admin panel: delete tag (with confirmation if tag is in use; cascade removes task_tag rows)
- [ ] Task create form: optional multi-select tag picker showing tag name + color swatch for each option
- [ ] Task edit form: same multi-select tag picker, pre-populated with existing tags
- [ ] Daily view task cards: colored left border stripe using the tag's color; if multiple tags, use the first tag's color for the stripe
- [ ] Daily view task cards: small Lucide icon for each tag rendered inline on the card
- [ ] Lucide icon library loaded via CDN (`https://unpkg.com/lucide@latest`) in base.html; icons rendered with `lucide.createIcons()`
- [ ] All existing tests continue to pass; new tests cover tag CRUD and task-tag association endpoints

**Notes / constraints:**
- Approved color palette (store as hex): dusty blue `#7eb8d4`, sage green `#8fbe8f`, warm terracotta `#d4856a`, soft lavender `#a89fd4`, amber `#d4b86a`, rose `#d48fa0`, steel teal `#6ab4b4`, slate violet `#8f9fd4`
- Icon name is a free-text string matching a valid Lucide icon (e.g. `home`, `briefcase`, `dumbbell`). No validation needed beyond showing the preview — if the icon name is invalid, Lucide silently skips it.
- If a task has multiple tags, left border uses the first tag's color; all tag icons are shown side by side
- Scheduling algorithm and task ordering are untouched
- Admin tag list should be reachable from the existing `/admin` dashboard

---

## In Progress

### Task tagging and categorisation
**Status:** in-progress
**Added:** 2026-06-23

---

## Done

### Recurring task delete modal
**Status:** done
**Added:** 2026-06-23

**Description:**
When deleting a recurring/variable_recurring/workout task from the daily view, show a modal with two choices: remove just today's projection row (task persists and continues on future days), or delete the entire task entity (current behaviour). Both actions produce an undo toast.
