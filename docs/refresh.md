# Automatic source refresh

Available starting in v0.2.0. This is a local Python worker,
not an LLM job or hosted service. Reading via MCP never starts acquisition.

## Configure and run

Use an existing store containing the selected collections:

```sh
DATA=/absolute/path/to/psephos-data
.venv/bin/psephos --data "$DATA" refresh configure uscode ecfr dc portland-guides \
  --interval-hours 168 --max-mib 512 --monthly-mib 8192
.venv/bin/psephos --data "$DATA" refresh run
.venv/bin/psephos --data "$DATA" refresh status
```

Select only sources you already acquired. A U.S. Code starter user should select
`uscode` alone. Other source adapters are not yet reviewed for unattended use:
fixed editions, partial selections and campaign-specific budgets need different
refresh handling. Configuration does not start acquiring new jurisdictions.

| Source | Exact scheduled scope |
| --- | --- |
| `uscode` | Current OLRC title inventory and bulk compilation |
| `ecfr` | Current eCFR title inventory and dated title XML |
| `dc` | D.C. Council code and law metadata from its pinned publication commit |
| `portland-guides` | The reviewed base-zone and overlay-zone guidance pages only |

Mutable URLs are conditionally revalidated using retained ETags/Last-Modified
when available. A server without validators may require another download.
Accepted unchanged federal title projections are not reparsed; identical D.C.
commit archives reuse accepted members. A changed payload creates a version,
without deleting old evidence. D.C. metadata-only laws stay metadata-only.

eCFR does not reliably supply cache validators. An unfinished scheduled cycle
reuses title bodies already checked within that cycle, for at most 24 hours,
while still checking the title inventory on every retry. Changed dated URLs
must be fetched. A successful cycle or the 24-hour cutoff starts a fresh check.
`last_success` is the completion of this bounded collection pass, not a claim
that every body was downloaded at that instant. Manual `sync --refresh` does
not reuse this cycle cache.

## Schedule

On macOS, using the same Python environment and store:

```sh
.venv/bin/psephos --data "$DATA" refresh install
.venv/bin/psephos --data "$DATA" refresh status
.venv/bin/psephos --data "$DATA" refresh pause
.venv/bin/psephos --data "$DATA" refresh uninstall
```

The per-user LaunchAgent wakes hourly while logged in. Each wake starts a finite
pass; only due sources run. It does not wake a sleeping/offline machine to fetch
law. `configure` enables a paused schedule again. Installation refuses a paused
configuration. The executable, virtual environment and store must remain at
their configured absolute paths. Moving them requires reconfiguration and
reinstallation, not copying the old schedule.

macOS can separately deny background Python access to Documents, even when it
works inside a terminal or Codex. Check the first job's log and exit status:
registration alone does not prove it ran. If it reports `Operation not permitted`,
uninstall the timer until the operator approves the appropriate Files and Folders
permission. Do not bypass that denial by switching runtimes or silently moving
the store. Full Disk Access is not a prerequisite of this design.

Match the permission to the timer's resolved interpreter: `Python.app` and a
Homebrew `python3.12` executable can have separate entries. Granting one does
not necessarily authorize the other. Recheck this after upgrading Python;
the executable's path may change even if the virtual environment's path does not.

The timer runs a supervisor that kills its worker after **30 minutes**, including
time inside parsers. Linux operators can schedule this same bounded entrypoint
with cron/systemd; no timer is installed there automatically:

```sh
/absolute/path/to/.venv/bin/python -m psephos.refresh_service /absolute/path/to/psephos-data
```

Apple documents per-user LaunchAgents and interval scheduling in
[Creating launchd Jobs](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html).

## Limits and failures

- The example checks weekly, allowing 512 MiB per pass and 8 GiB per UTC calendar month. Accounting
  charges decoded response bytes plus 4 KiB per request; it is not a measurement
  of exact wire traffic. Rejected payload chunks remain charged. Allowance is
  reserved before HTTP; interrupted processes do not refund unknown transfers.
- Every source has at most 100 requests and a 15-minute window for starting
  requests. The supervisor's separate 30-minute process deadline is the hard
  limit. A source checks for at least 8 GiB free before starting. Existing data
  is never deleted to make room.
- Successful checks recur after the configured interval. Temporary failures
  back off for 1, 2, 4, 8, 16, then 24 hours. Partial/failed inventory members
  cannot advance `last_success`. Unexpected parser/schema failures block their
  source, not the other sources.
- HTTP 401/403/407/451, proxy denials and robots prohibitions stop the source.
  No alternate route is tried. `refresh retry SOURCE` requests operator review
  and another attempt, but does not bypass retained denials or reset spending.
  A permitted successful manual check is needed to supersede a retained HTTP
  denial. Do not retry denied publisher access without authorization.
- Inventory removal is compared against the previous successful refresh only.
  The first successful check establishes this baseline; it cannot establish
  whether earlier retained items disappeared. Later removals block for review,
  not silently mark historical documents repealed.
- State and any configured HTTPS proxy live privately in `data/refresh/`.
  A changed proxy environment stops the worker instead of silently switching
  routes. Reconfigure after an intentional change; reinstall the timer to update
  its captured environment. Do not copy or reset the state to regain allowance.
- `service.log` rotates on a later run after exceeding 2 MiB, keeping one previous
  log. The state preserves operational status; the catalog keeps source receipts.

`refresh status` and MCP source cards distinguish checked, overdue, blocked,
retryable, interrupted and unscheduled sources. A successful publisher check is
**not** an effective date, a new legal snapshot date, comprehensive jurisdiction
coverage, or a promise that all law is current. Unscheduled sources are explicitly
labelled, not assumed fresh.

The refresh worker, public `sync` commands and offline `reindex` share a
nonblocking writer lock.
Before backups, replay, imports or legacy maintenance scripts, let the current
worker finish, then pause the timer. Those scripts are not all covered by this
lock. Portable exports omit private refresh state and download allowances.
