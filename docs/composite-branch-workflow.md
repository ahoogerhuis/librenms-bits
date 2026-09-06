# Running a Composite Branch: Upstream `master` Plus Local Feature Branches

A workflow for a live LibreNMS install that needs to track real upstream `master` *and* carry one or more feature branches that haven't merged upstream yet — a personal fix still under review, a patch you depend on that hasn't landed, an experimental change you're evaluating. Standard LibreNMS update tooling assumes a plain, fast-forward-only checkout of `master`; this covers what changes once that stops being true.

## Contents
- [The core pattern](#the-core-pattern)
- [Staying current with upstream](#staying-current-with-upstream)
- [The update-script gotcha](#the-update-script-gotcha)
- [What LibreNMS's own daily.sh and validate.php actually do](#daily-sh-and-validate-php)
- [Conflict risk and merge order](#conflict-risk-and-merge-order)
- [Worked example](#worked-example)

<a id="the-core-pattern"></a>

## The core pattern

Three things, kept deliberately separate:

1. **A second remote, pointing at wherever your feature branches live** (a personal fork, a colleague's fork, an internal mirror) — alongside the existing `origin` remote that already points at real upstream.
2. **A dedicated branch for the merged result — never `master` itself.** `master` stays a clean, untouched mirror of real upstream at all times, so there's always a known-good fallback to switch back to if the composite branch ever ends up in a state you don't trust.
3. **Feature branches merged in one at a time**, not all at once — see [Conflict risk and merge order](#conflict-risk-and-merge-order) for why this matters.

This is an ordinary merge, not a rebase, and not a cherry-pick chain — the composite branch just accumulates real merge commits over time, same as any other long-lived integration branch.

> **Don't build the second remote on a churn-prone scratch fork.** If your infrastructure already has a fork used for AI-agent experimentation or PR preparation — one that gets rebased or reset routinely as part of that work — don't point a live install's composite branch at it. A live install pulling continuously from a branch that gets rewritten out from under it will break on the very next rebase. This needs its own dedicated fork/branch, used for nothing else.

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

<a id="daily-sh-and-validate-php"></a>

## What LibreNMS's own daily.sh and validate.php actually do

Checked directly against LibreNMS's real source, and verified live against a real composite branch on a test install — worth confirming rather than reasoning about generically, since both of the following look more concerning on first read than they turn out to be in practice.

**`daily.sh`'s decision to update at all is driven by an exit code from `php daily.php -f update`**, confirmed live against a real current install (not just read from source):

- **`0`** — no code update this run: updates disabled outright, today isn't in the configured `update_on_days`, or the channel is unset. Only schema migration and routine cleanup happen.
- **`2`** — channel is `master`: `daily.sh` runs a plain `git pull` on whatever branch is currently checked out.
- **`3`** — channel is `release`: instead of pulling anything, it fetches tags, compares versions, and `git checkout`s the latest tag's commit hash directly — landing on a detached HEAD, not a branch at all.

There's no `lnms self-update` command or equivalent standing behind any of this. `daily.sh` *is* the entire update mechanism, not a wrapper around something else.

**This is why `update_channel` has to stay `master` for a composite branch, never `release`.** The release path has no concept of a branch to follow — it looks for the newest upstream *tag* and checks that specific commit out directly, detaching HEAD in the process. A continuously-merged composite branch isn't a tag and never will be, so the release path has nothing to find there; only the `master`-channel branch-following path (a plain `git pull`) has any chance of ever picking up the composite branch's own commits.

**`daily.sh` runs a plain `git pull --quiet`** — no `--rebase`, no `--ff-only`. Worth being explicit about what it does *not* check, too: nowhere in the script is there any awareness of which remote or repo `origin` actually points at — a bare `git pull` just follows whatever the current branch's tracked upstream is in local git config. That's exactly why this whole composite-branch pattern works without touching LibreNMS's own code at all: the update mechanism was never checking repo identity to begin with, only branch state. On a genuinely diverged composite branch, the actual behavior depends on your own git config (the default is fetch-then-merge), and the real risk is a genuine merge conflict happening unattended during an automated nightly run, not the script itself doing anything unexpected. `daily.sh` does have a failure-notification path that fires on a non-zero exit, so a conflict during an automated run won't go completely unnoticed — but it's still worth switching the automated step to the explicit `git fetch` + `git merge origin/master` shown above, rather than leaving it to `git pull`'s default behavior once the branch has diverged.

**`./validate.php -g updates` will show two things on a composite branch, both expected, neither a real problem:**

1. **A branch-name warning, every time, by design:**
   ```
   [WARN]  Your local git branch is not master, this will prevent automatic updates.
       [FIX]:
       You can switch back to master with git checkout master
   ```
   This is a direct, unavoidable consequence of never touching `master` itself — LibreNMS's own validator checks the branch name literally, with no exception for a deliberately-composite setup. It isn't a sign anything is wrong; it's LibreNMS correctly observing that you're not on `master`, exactly as this workflow intends.

2. **A staleness check that looks like it should produce a permanent false alarm, but doesn't.** The check compares your local commit hash against the live upstream `master` tip fetched directly from GitHub's API — not your local `origin/master`, not whatever branch happens to be checked out, the real upstream tip specifically. On a composite branch this comparison is essentially always a mismatch, since a merge commit's hash can never equal a plain upstream commit's hash. Read in isolation, that sounds like it would produce a permanent "out of date" warning. It doesn't, because that hash comparison is only a gate — the actual warning fires only if your **local commit's own timestamp** is more than 24 hours old, regardless of what the hash comparison found:
   ```
   [WARN]  Your install is over 24 hours out of date, last update: <date>
       [FIX]:
       Make sure your daily.sh cron is running and run ./daily.sh by hand to see if there are any errors.
   ```
   A composite branch's own merge commit is timestamped the moment the merge runs — so merging `origin/master` in at least once within any 24-hour window resets that clock every time, and this warning never fires for a branch that's actually being kept current. Confirmed live, both directions: a fresh merge on a real composite branch produced no staleness warning at all (only the branch-name warning above), and deliberately backdating a test commit to two days old reproduced the exact warning shown, confirming the check genuinely works — it's just measuring "was there a recent commit," not "are you behind upstream," which happens to be exactly the right signal for a branch maintained this way.

   This is exactly why the [upstream fetch+merge step](#staying-current-with-upstream) is worth running on an actual schedule rather than only by hand when someone remembers to: if that job stops running, the composite branch's newest commit ages past 24 hours and this warning starts firing for real — correctly surfacing that the update pipeline itself has stalled, rather than being false noise to tune out.

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
# Make sure the install is actually set to follow a branch, not a tag --
# see "What LibreNMS's own daily.sh and validate.php actually do" for why
# `release` doesn't fit a composite branch at all.
./lnms config:set update_channel master
```

Staying current with upstream afterward, on an ongoing basis:

```bash
git fetch origin
git merge origin/master
```

Adding a second feature branch later follows the same pattern — fetch its remote if it isn't already added, then `git merge mine/other-branch-name` as its own separate step, so it's clear on its own whether it merged cleanly.

If the composite branch ever ends up in a state you don't trust, the fallback is immediate: `master` was never touched, so `git checkout master` gets back to a known-good, real-upstream state at any time.
