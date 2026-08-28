# Legislative patch oracle feasibility

**Verdict: KILL V0 on the evidence established here. Wider-population feasibility remains
unresolved.** The retained Psephos corpus contains zero complete official base + applied amendment
+ successor chains, so there is no honest episode or grader target to build. An online GovInfo
search suggested a larger candidate pool, but those result bytes were not retained and 458 remote
rows were not screened. This report does not turn “not proved” into “proved impossible.”

That distinction is the central result. The mandatory 25-example gate was not demonstrated, so the
correct action is to stop before architecture grows. No public episode, hidden set, grader, oracle
target, or baseline program was created.

The measurements are in
[`legislative-patch-feasibility.json`](legislative-patch-feasibility.json). The exact local sample
bytes are receipted in
[`legislative-patch-source-manifest.json`](legislative-patch-source-manifest.json).

## Gate result

| Mandatory gate | Required | Established | Status |
|---|---:|---:|---|
| Unambiguous exact triples | 25 | **0 in retained corpus; wider population unknown** | Not demonstrated |
| Structurally distinct admitted operations | 3 | **0** | Not demonstrated |
| Chamber coverage in admitted triples | House + Senate | **Neither** | Not demonstrated |
| Held-out shortcut resistance | Yes | **0 eligible cases** | Not demonstrated |
| Evidence for hundreds/thousands of triples | Yes | **0 witnessed examples** | Not demonstrated |

The baseline scores for copy-base, copy-amendment, regex patching, and public hardcoding are `null`.
Assigning numbers without an admitted official target would fabricate evidence.

## Exact local evidence

The Time Machine holds six files acquired from GPO's official USLM GitHub sample-conversion
directory. They cover six distinct bill identities and 129,945 bytes. All six were re-hashed locally
against the existing manifest with no mismatch.

These are **exact bytes retrieved from GPO's official USLM sample repository**, not proven
byte-identical copies of the current GovInfo
package URLs. No GovInfo PREMIS digest or production-package body was retained for comparison. All
six publication times, HTTP ETags, and HTTP Last-Modified values are missing; acquisition times are
present. The GitHub acquisition URLs use a mutable `main` ref rather than a commit; the content hashes
fix the observed bytes and will expose drift, but cannot reconstruct the original repository revision.
The receipt says these limits explicitly rather than implying stronger fixity.

Five files are isolated bill/resolution samples. One is an amendment sample:

| Package | Root | Bytes | SHA-256 | Locally witnessed content |
|---|---|---:|---|---|
| `BILLS-116hr1865eas` | `engrossedAmendment` | 3,796 | `c2bd2db…291731` | One “At the end, add…” instruction and one `changed="added"` payload |

The six files range from 3,113 to 75,722 bytes. None of the other five is a base or successor for
H.R. 1865. The local corpus contains no raw BILLSTATUS XML, no GPO bill/amendment schema file, no
multi-version chain, and zero canonical `amendments` rows. Thus the exact local counts are:

- amendment samples: 1;
- exact bases for those samples: 0;
- exact successors for those samples: 0; and
- admitted triples: **0**.

The legacy BILLSTATUS derivatives do not fill the gap. They contain 106,592 metadata rows, but raw
official responses were discarded and their rows omit the action, amendment, and text-version
evidence this oracle needs. A derivative record hash cannot authenticate unretained source bytes.

## Online candidate observation—not a retained inventory

On 2026-08-28, the GovInfo search UI reported:

| Query | UI-reported rows |
|---|---:|
| `billversion:eas congress:range(113,119)` | 288 |
| `billversion:eah congress:range(113,119)` | 171 |
| Combined stage-code rows | **459** |

An unrestricted EAS query reported 1,344 rows. The search responses, exact result URLs, and result
bodies were not retained, so these figures are **unreceipted online observations** and are not
reproducible offline from this repository. They are useful only as evidence that targeted acquisition
might be worthwhile—not as a denominator of screened triples or proof of scale.

The population is also heterogeneous. GovInfo defines EAS as the official copy of a Senate amendment
as passed. EAH is the official copy of a bill or joint resolution as passed by the House, including
floor amendments. Both can be whole-text substitutes. A BILLS stage-code result is therefore not
automatically a standalone executable amendment instruction. See the official
[BILLS definitions](https://www.govinfo.gov/help/bills) and
[BILLS XML guide](https://github.com/usgpo/bulk-data/blob/main/Bills-XML-User-Guide.md).

No online source body was added to the repository during this mission. New retained network bytes
were zero, so the 500 MiB metadata and 5 GiB pilot ceilings were respected. Existing sample bytes
were re-hashed in place rather than copied.

## The one screened local case

The local H.R. 1865 sample demonstrates why chronology is not causality. It carries a November 12,
2019 Senate instruction to append section 9. A naive pipeline could pair that instruction with a
House-passed/received base and call the enrolled package its successor.

Official pages observed during the audit report an intervening event: on December 17 the House
agreed to the Senate amendment with a further amendment consisting of Rules Committee Print 116-44;
on December 19 the Senate agreed to that House amendment. The relevant official online references
are the [Congress.gov action history](https://www.congress.gov/bill/116th-congress/house-bill/1865/all-actions),
the [House amendment PDF](https://www.congress.gov/116/bills/hr1865/BILLS-116hr1865eah.pdf), and the
[enrolled GovInfo package](https://www.govinfo.gov/app/details/BILLS-116hr1865enr/related).

Those action/PDF bytes were not retained in the repository or content manifest, so this is recorded
as an unreceipted official-source observation, not an offline oracle receipt. The local case is
excluded regardless because its exact base and final-target bytes are absent. The observed
intervening amendment supplies a second reason not to infer the enrolled target from the local EAS
instruction alone.

## Why metadata does not supply the missing edge

The primary-source models contain useful identifiers but do not, by themselves, declare the complete
relation needed here:

- GovInfo BILLS package IDs identify a bill and stage; related versions are not directed patch edges.
- BILLSTATUS includes versions, actions, amendment identities and targets. GPO documents that action
  codes are source-dependent representations and that no complete authoritative code list exists;
  record-level action history still has to be interpreted conservatively. See the official
  [BILLSTATUS guide](https://github.com/usgpo/bill-status/blob/main/BILLSTATUS-XML_User_User-Guide.md).
- Congress.gov exposes amendment targets and action histories. For text, the official API notes full
  searchable Senate submitted-amendment text from the 117th Congress forward; older Senate and many
  House amendments rely on Congressional Record links, and only Senate plus some House amendments
  from the 117th forward expose text versions. See the
  [amendment endpoint documentation](https://github.com/LibraryOfCongress/api.congress.gov/blob/main/Documentation/AmendmentEndpoint.md).
- GPO's amendment DTD makes `base-document-id` optional and defines it only as an XML `ID`, not a
  GovInfo package reference. The DTD also has a more promising optional `meta-aip` structure for
  executed status, application order, filenames, and optional hashes. That is schema capability, not
  production evidence: none was found in the retained samples, and no filename-to-retained-byte path
  was established. See [`amend.dtd`](https://github.com/usgpo/bill-dtd/blob/master/amend.dtd).
- USLM has structured action and amending attributes, but the retained sample uses prose plus
  `changed="added"`; it does not carry a complete executable base/successor relation. See the
  [USLM guide](https://github.com/usgpo/uslm/blob/main/USLM-User-Guide.md).

Sorting package dates cannot repair this. Nor does an exact diff alone prove that the observed
amendment set was complete: later chamber amendments, vitiated actions, corrections, star prints,
conference work, or enrollment changes can contaminate the target.

## What a future triple would have to prove

A candidate may be admitted only when all of these are retained and machine-checked:

1. exact official base, amendment, and successor bytes with source URL, SHA-256, byte count,
   publication metadata, and acquisition time;
2. an official identifier resolving the amendment to the base plus adoption/action evidence;
3. official evidence that the supplied amendment set is complete and ordered for the transition;
4. no failed, withdrawn, tabled, vitiated, modified, conference, correction, or unobserved
   intervening amendment;
5. unique anchors and deterministic instruction order;
6. normalized structural equality to the successor, exact preservation of unaffected regions, and
   no unexplained residual diff; and
7. no target leakage through a whole-measure substitute or candidate-visible successor copy.

Only append/insert is evidenced by retained bytes. Strike/replace, whole-measure substitution,
multiple line edits, and nested amendment chains appeared in unretained search snippets; they remain
hypotheses and are excluded from operation-family counts. Cosmetic variants and repeated templates
must collapse to one procedure.

## Reusable probe and receipts

[`patch_probe.py`](../src/time_machine/patch_probe.py) is intentionally not a crawler. It:

- re-hashes the six existing sample objects without copying them;
- acquires one explicit allowlisted federal URL into a cumulative capped SHA-256 store;
- labels API, JSON-index, and BILLSTATUS responses as `metadata_only`;
- grants exact bill/amendment XML semantics only after package-ID, URL, media-type, safe XML parse,
  and root checks; validates schema media/structure; refuses to redownload a source URL under a new
  identifier; and
- verifies receipt totals, artifact IDs, SHA-addressed paths, official URLs, timestamps,
  relationships, semantic invariants, duplicate rows, hashes, and sizes.

The content-addressed source manifest is local-cache-specific: `data/time_machine` is not included in
a clean clone. Re-running the inventory command in an emailed repository therefore requires the same
local data cache. It is a re-hash receipt, not a self-contained redistribution of 129,945 raw bytes.

Bootstrap a fresh development environment before using the module:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Re-hash the existing local cache:

```bash
.venv/bin/python -m src.time_machine.patch_probe inventory \
  --time-machine-root data/time_machine \
  --output docs/legislative-patch-source-manifest.json
shasum -a 256 docs/legislative-patch-source-manifest.json
```

Expected summary and manifest hash:

```text
{"artifacts": 6, "by_kind": {"official_gpo_sample_amendment_xml": 1, "official_gpo_sample_bill_version_xml": 5}, "bytes": 129945, "published_at_missing": 6, "published_at_present": 0}
cb789719241de4dd28e5f946e65625b3cc703d6dc40b268cac10b363ef91f9b4
```

The smallest metadata-first acquisition for the screened identity is:

```bash
.venv/bin/python -m src.time_machine.patch_probe acquire \
  --store-root data/legislative_patch_probe \
  --url https://www.govinfo.gov/bulkdata/BILLSTATUS/116/hr/BILLSTATUS-116hr1865.xml \
  --identifier BILLSTATUS-116hr1865 \
  --kind billstatus_metadata_xml \
  --relationship describes=bill:us:116:hr:1865 \
  --cap-mib 500
.venv/bin/python -m src.time_machine.patch_probe verify \
  --store-root data/legislative_patch_probe
```

This command is documented but was not run; no network bytes were retained. A caller can explicitly
raise the cap to 5,120 MiB for a future gate-passing pilot, but the utility rejects anything larger.

Focused verification actually run:

```bash
PYTHON=/Users/matthew/Documents/Coding/psephosamerica/.venv/bin/python
"$PYTHON" -m pytest -q tests/test_legislative_patch_probe.py
"$PYTHON" -m ruff check \
  src/time_machine/patch_probe.py tests/test_legislative_patch_probe.py
"$PYTHON" -m mypy --follow-imports=skip src/time_machine/patch_probe.py
```

The suite has 13 focused tests covering local re-hash truth, absence of inferred successor edges,
official-host/secret rejection, metadata labeling, exact-XML validation, content addressing,
cumulative cap behavior, idempotent resume, source-URL deduplication, schema rejection, missing
receipts, and tampered totals.

| File | SHA-256 |
|---|---|
| `src/time_machine/patch_probe.py` | `86876d718f08c641d6ccf166a5e48fd28a837ada23d46c1f47d3f8ae650b4286` |
| `tests/test_legislative_patch_probe.py` | `665858c2475d0ad9902454689b93caf29f4ae2003470c3c561ead897a1297461` |
| `docs/legislative-patch-source-manifest.json` | `cb789719241de4dd28e5f946e65625b3cc703d6dc40b268cac10b363ef91f9b4` |

## Redistribution, authentication, and decision

The retained U.S.-government XML sample carries an explicit 17 U.S.C. 105/public-domain notice.
GPO's guidance says bill data generally has no downstream reuse restriction, while only GPO and its
partners may present themselves as providers of official versions. XML itself is unsigned; a future
bundle should preserve URLs and hashes and link to authenticated PDFs and PREMIS fixity metadata
where available. Embedded third-party material must be screened separately. See the official
[GovInfo policies](https://www.govinfo.gov/about/policies) and BILLS XML guide.

**Final decision: KILL V0; retain only this report, the machine companion, the source receipt, and the
probe.** Reopening the hypothesis requires targeted raw metadata and document acquisition followed by
exact replay. The threshold remains 25 admitted triples across three collapsed operation families.
Until that work is done, the wider hypothesis is unresolved—not dead, and not a benchmark.
