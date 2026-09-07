# Washington: statutes → SEPA/GMA rules → filing evidence

This adds a bounded, locally readable Washington land-use source family to the
existing Store/Reader/MCP library. It does not decide what law applies to a project,
apply amendments, or claim statewide regulatory completeness.

## Accepted scope

Measured September 7, 2026 UTC; [machine-readable receipts and verification](washington-land-use-verification.json).

| Publication | New documents | Stored units | Scope |
| --- | ---: | ---: | --- |
| Live WAC chapters | 9 | 344 | 335 rule sections + 9 contents/disposition units |
| RCW chapter 43.21C | 1 | 76 | 75 statutory sections + chapter notes; SEPA authority |
| WSR filings | 3 | 3 | Complete retained filing HTML, not codified current text |
| WSR indexes/legend | 4 | 4 | Two affected-title tables, parent index, action legend |
| Commerce notice | 1 | 1 | Agency rulemaking-status page, not a statute or filing |

The nine complete WAC chapters are **197-11** and **365-185, 365-190, 365-191,
365-195, 365-196, 365-197, 365-198, 365-199**. The official current indexes list
one chapter in Title 197 and 29 in Title 365. All those chapter rows are inventoried;
21 Title 365 chapters remain outside this selection. The root inventory also records
all 197 WAC title rows. Thus 227 inventory items mix title and chapter records:
9 ingested chapters, 2 inventoried titles, 216 outside-scope items—not 227 laws.
Former chapters mentioned only in reviser's notes remain source evidence, not
invented current chapter entries. No Title 51/model-building-code acquisition.

There are 18 new documents/428 mixed units, not 428 regulations. The 27 directly
used source objects total **6,208,155 bytes**. New distinct retained bytes during
preflight/acquisition were **6,165,979**, including publisher/robots metadata;
existing receipts were reused. These are source-byte counts, excluding SQLite/FTS
storage. Raw publisher files remain in ignored local storage, not public Git.

## Read it offline

With this retained store, use the existing MCP tools:

```text
legal_coverage(jurisdiction="us-wa")
legal_coverage(collection="wa-wac", view="documents", limit=2)
legal_coverage(collection="wa-wac", view="inventory", status="outside_scope", limit=5)
legal_search(query="Content of environmental review", collection="wa-wac", limit=3)
legal_read(key_or_id="wa-wac:197-11-060", length=1000)
legal_find(key_or_id="wa-wac:197-11-060", query="Phased review is not appropriate")
```

Read from the returned match offset to retain the qualifications. Pass the returned
provision ID to `legal_references`. Its actual authority citation resolves to the
acquired `wa-rcw:43.21C.110`; same-chapter links resolve to acquired WAC provisions
such as `wa-wac:197-11-330`. The 1997/1984 filing citations remain explicit
unacquired dependencies. A missing target is not permission to ignore it.

For a second path, read `wa-wac:365-195-900` and follow its filing citation to
`wa-wsr:26-01-181`. The final filing explicitly identifies proposal
`wa-wsr:25-13-090`. Follow reference pagination to reach the earlier filing.
Read `wa-wsr:table:2025:365` and `wa-wsr:action-legend` alongside them: the table
records `AMD-P` versus `AMD`; its legend distinguishes proposal, continuance,
emergency, withdrawal and other action types. The final filing expressly says
365-196-840 was not adopted. Neither the proposal nor a table row is a replacement
for current rule text.

Affected-table metadata exposes the native row count and an explicit omission
notice instead of repeating every action row in both provision and version
metadata. The complete rows remain available through paginated text/markup,
`legal_find` and `legal_references`; stored metadata and source bytes are unchanged.

Filing deletions remain `[DELETED]…[/DELETED]`; source underlining remains
`[UNDERLINED]…[/UNDERLINED]`. This is a display annotation, not a conclusion about
legal effect. Full markup and original bytes survive. Media descriptors and
reader completeness warnings remain visible rather than inventing missing content.
The proposal's one external image is not acquired; its descriptor and source link
remain visible. Complete retained HTML is not a claim of complete external media.

### Exact, limited citation resolution

Native HTTP RCW citations now match the actually acquired HTTPS section records.
Only documented RCW/WAC endpoints, citation forms and same-chapter WAC fragments
are supported. Raw link/label/evidence is unchanged. WSR citations require an exact
acquired filing key and an official file URL matching the retained acquisition;
no file URL is invented from a bare filing number. Lookalikes, ambiguous matches,
extra/duplicate query parameters and unsupported representations do not resolve.
Existing independent snapshot/observation cutoffs also apply to targets.

Long filing reference pages rank only the candidate documents containing an exact
key, not the entire national version catalog once per link. All versions of those
documents still participate: a provision removed from the latest document must
not reappear merely because its older key was found.

## Currency and conflicts remain explicit

- The [WAC publisher overview](https://leg.wa.gov/state-laws-and-rules/state-rules-wac/)
  describes twice-monthly compilation; the retained live root displays August 28,
  2026. The [certified annual PDF archive](https://leg.wa.gov/state-laws-and-rules/state-rules-wac/past-versions-of-state-rules/)
  is a separate official publication. Live HTML is not labeled certified or an
  immutable August 28 snapshot. These new live versions have no invented snapshot
  or legal-effective date; their actual observation clocks are recorded.
- A 2026 Register identifier is not a 2026 filing date: **26-01-181 was filed
  December 23, 2025**. Filing-header dates and effective-date wording are retained;
  unknown issue-publication dates stay unknown. An observation in September is
  not a retroactive claim that Psephos possessed the bytes in January.
- The [2026 affected-section index](https://lawfilesext.leg.wa.gov/law/wsr/2026/table-26.htm)
  says it covers filings through August 5, but its actual Title 197/365 links lead
  to pages labeled **2025**. Title 365 includes the cross-year filing 26-01-181;
  Title 197 says no filings **in that linked table**, not no SEPA rulemaking ever.
  Parent and child scope statements are retained, not silently reconciled.
- [Commerce's notice](https://www.commerce.wa.gov/about/legislative/rulemaking/)
  characterizes some rules as obsolete and describes June 2026 intent to revise
  GMA rules. Those are agency statements, not our determination that existing
  provisions lost legal effect. The notice says June 6 for 365-199-100;
  [WSR 26-10-066](https://lawfilesext.leg.wa.gov/law/wsr/2026/10/26-10-066.htm)
  and the WAC note say June 5. Similarly, Commerce says January 24 for the climate
  rules while [26-01-181](https://lawfilesext.leg.wa.gov/law/wsr/2026/01/26-01-181.htm)
  says January 23. These are separate attributed evidence, not an automated ruling.

No linked Box files, videos, every cited statute, local ordinances, Seattle GIS,
historical archive campaign, or incorporation dependencies were silently added.
Only the missing SEPA authority chapter 43.21C was added to the existing RCW family;
its original selected-title metadata remains, with a supplemental-chapter note.

## Acquisition and verification

The maintained pure parsers and bounded driver are in
[`washington_rules.py`](../src/psephos/washington_rules.py); the existing CLI exposes:

```sh
.venv/bin/psephos --data data sync washington-land-use
.venv/bin/psephos --data data status --collection wa-wac
.venv/bin/python scripts/verify_washington.py --data data --check-resume --out /path/to/new-wa-receipt.json
```

Acquisition uses one controlled writer, the existing Acquirer cache/hash/rate/robots
policy, at least 1.1 seconds per host, a 16 MiB per-run driver cap and 2 MiB per-file
cap. Inspect the current publisher policies before a future refresh. This run
checked live robots/terms and native DOMs first; 404 robots responses use the
existing no-policy rule. The obsolete linked legend returned 404, and its current
[official location](https://leg.wa.gov/media/vvxb5flh/keytotable.htm) was found and
retained separately. No denied endpoint was bypassed. Copyright/sale notices were
retained; public availability is not treated as permission to redistribute or sell.

The verifier is scoped to this acquisition receipt, not a whole-corpus audit.
It rehashes the used source artifacts and proves exact re-parsing of **13 primary
documents/423 units** (all fields, markup, hierarchy and stored reference evidence).
The remaining five units are indexes/legend/agency context, not additional primary
rule bodies. Its optional resume runs the actual driver with a transport that
fails any network attempt: **zero new documents, versions, acquisitions or bytes**.
Inventory check timestamps may advance. Florida/New Jersey annotations stay intact.

The initial **22-call stdio MCP path passed in 3.283 seconds** in one observed run,
including the qualification, authority, rule, proposal/final, action-table, source
conflict, raw-hash and cutoff checks. After that run, a narrow output-only fix
removed duplicated bulk action rows. The one affected 500-character table read
was rechecked through actual MCP: **8,052 structured response bytes**, down from
47,086, with row/action evidence still readable and stored metadata unchanged.
The receipt keeps the initial run and final affected-call recheck, including their
different code hashes, as separate verification stages. No acquisition or full
MCP/resume run was repeated for this output change.

These checks are neither a latency distribution nor a legal-completeness
certification. Focused tests also cover malformed sources,
missing bodies, redlines, action types, hostile citation forms, ambiguity and key
removal across snapshots. No earlier campaign or frozen replay was rerun.
