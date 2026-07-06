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

---

## In Progress

<!-- Claude is currently working on these -->

---

## Done

### Per-task notifications + push delivery fixes
**Status:** done
**Added:** 2026-07-06

**Description:**
Optional per-task notifications: attach one or more to any appointment, each firing at the scheduled time or N minutes before (stored as offsets in `task_notifications`, so rescheduling moves them). Also hardened delivery: dead subscriptions auto-pruned, `pushsubscriptionchange` re-subscribe, `POST /push/test` debug endpoint, all devices attempted per send. Delivery to Android confirmed working 2026-07-06 — required HTTPS via `tailscale serve` and converting the PEM-formatted VAPID private key in `.env` to raw base64 (see README).

### Push notifications
**Status:** done
**Added:** 2026-06-25

**Description:**
Background push notifications on Android for appointments. Bell icon in header to subscribe/unsubscribe. Notification fires at `prep_duration` minutes before the appointment (or 30 min default). VAPID keys stored in `.env`.

### Task tagging and categorisation
**Status:** done
**Added:** 2026-06-23

### Recurring task delete modal
**Status:** done
**Added:** 2026-06-23

**Description:**
When deleting a recurring/variable_recurring/workout task from the daily view, show a modal with two choices: remove just today's projection row (task persists and continues on future days), or delete the entire task entity (current behaviour). Both actions produce an undo toast.
