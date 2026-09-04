# Running a Composite Branch: Upstream `master` Plus Local Feature Branches

A workflow for a live LibreNMS install that needs to track real upstream `master` *and* carry one or more feature branches that haven't merged upstream yet — a personal fix still under review, a patch you depend on that hasn't landed, an experimental change you're evaluating. Standard LibreNMS update tooling assumes a plain, fast-forward-only checkout of `master`; this covers what changes once that stops being true.

## Contents
- [The core pattern](#the-core-pattern)
- [Staying current with upstream](#staying-current-with-upstream)
- [The update-script gotcha](#the-update-script-gotcha)
- [Conflict risk and merge order](#conflict-risk-and-merge-order)
- [Worked example](#worked-example)

<a id="the-core-pattern"></a>

## The core pattern

Three things, kept deliberately separate:

1. **A second remote, pointing at wherever your feature branches live** (a personal fork, a colleague's fork, an internal mirror) — alongside the existing `origin` remote that already points at real upstream.
2. **A dedicated branch for the merged result — never `master` itself.** `master` stays a clean, untouched mirror of real upstream at all times, so there's always a known-good fallback to switch back to if the composite branch ever ends up in a state you don't trust.
3. **Feature branches merged in one at a time**, not all at once — see [Conflict risk and merge order](#conflict-risk-and-merge-order) for why this matters.

This is an ordinary merge, not a rebase, and not a cherry-pick chain — the composite branch just accumulates real merge commits over time, same as any other long-lived integration branch.

<a id="staying-current-with-upstream"></a>

## Staying current with upstream

Update the composite branch with:

```bash
git fetch origin
git merge origin/master
```

**Not** `git rebase origin/master`, and not `git pull` once the branch has diverged (see the next section for why `git pull` specifically becomes a problem). A rebase rewrites the composite branch's commit history every time upstream moves — on a one-off feature branch that's fine, but a live install's branch is checked out and running continuously; repeatedly force-pushing and resetting it to a rewritten history is exactly the kind of disruption a live checkout doesn't need, for no benefit over a plain merge. A merge only ever adds commits — the existing history, including your own feature-branch merges, is never rewritten or force-pushed.

<a id="the-update-script-gotcha"></a>

## The update-script gotcha

Whatever currently updates the live install on a schedule (a nightly update script, a cron job, LibreNMS's own daily maintenance script) most likely assumes a fast-forward-only `git pull` — the standard "plain `master`, no local changes" case. That assumption breaks the moment the branch carries real local merge commits: a fast-forward is no longer possible, and a plain `git pull` will either fail outright or (worse, depending on the configured pull strategy) attempt an unexpected rebase or merge at a moment nobody's watching for the result.

**Read the actual update script before switching a live install over — don't assume it either already handles this or will fail loudly if it doesn't.** Find wherever the update mechanism calls its git commands and confirm what it actually does; if it invokes a plain `git pull` (or relies on `git pull`'s default behavior), change that step specifically to `git fetch` + `git merge origin/master`, matching the manual command above, rather than leaving it to whatever `git pull`'s configured default does once the branch has diverged.

<a id="conflict-risk-and-merge-order"></a>

## Conflict risk and merge order

Merging every feature branch into the composite branch in one combined step makes a conflict much harder to diagnose than merging them in one at a time: if three branches go in together and something conflicts, it's not immediately obvious which branch is actually responsible. Merging one at a time means any conflict that appears is unambiguously caused by the branch just merged.

The branches themselves aren't equally risky. A branch that only adds new files — a new poller module, a new documentation page, a new test fixture — has effectively no way to conflict with anything else, since nothing else touches those paths. A branch that modifies a file other branches (or upstream itself) also touch is a different story:

- **Low risk, purely additive**: a branch adding `includes/polling/new-module.inc.php` and `docs/NEW_MODULE.md` — files that exist only because this branch created them. Nothing else can conflict with a file nobody else touches.
- **Higher risk, shared-file changes**: a branch modifying `includes/html/pages/apps.inc.php` (a large, frequently-touched core file) to register a new entry. If upstream `master` also changes that file before this branch merges — even in a completely unrelated part of it — a normal three-way merge usually resolves it cleanly on its own, but a real conflict becomes possible the moment two changes land near the same lines.

Knowing which kind of branch you're merging changes how much attention the merge needs: a purely-additive branch can usually be merged and moved past without much scrutiny, while a branch touching shared files is worth a real look at the merge result (or a quick diff against what the file looked like before) rather than assuming a clean merge means a correct one.

<a id="worked-example"></a>

## Worked example

Starting from a normal `master`-tracking checkout of `librenms/librenms`, adding one feature branch (`feature-branch-name`, living on a second remote called `mine`) on top:

```bash
# One-time setup: add the second remote and create the composite branch
git remote add mine <url-of-the-fork-or-mirror-holding-the-feature-branch>
git fetch mine
git checkout -b composite master

# Bring in one feature branch
git merge mine/feature-branch-name
# Resolve here if it conflicts -- you know exactly which branch caused it,
# since this is the only thing being merged in this step.

# Deploy/run from `composite`, not `master`, from this point on.
```

Staying current with upstream afterward, on an ongoing basis:

```bash
git fetch origin
git merge origin/master
```

Adding a second feature branch later follows the same pattern — fetch its remote if it isn't already added, then `git merge mine/other-branch-name` as its own separate step, so it's clear on its own whether it merged cleanly.

If the composite branch ever ends up in a state you don't trust, the fallback is immediate: `master` was never touched, so `git checkout master` gets back to a known-good, real-upstream state at any time.
