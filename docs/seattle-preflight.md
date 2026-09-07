# Seattle: acquisition paused, no coverage published

The September 7, 2026 [preflight receipt](seattle-preflight.json) retains four
official ArcGIS item-metadata objects, two Legistar legislative-detail pages and
one robots policy: seven successful objects, 372,585 bytes. These are not zoning
polygons, lookup rows or enacted ordinance text. No Seattle collection was published.

The existing acquirer stopped when `https://services.arcgis.com/robots.txt`
returned HTTP 403. No service feature query, Hub/proxy export, policy exception or
access workaround followed. A separate bounded search by the coordinating task
found no suitable independently published City-owned static export; that is not
a claim that none can exist. Unused publisher parameterization/fixtures are parked
outside the shipped runtime, not presented as a Seattle implementation.

Only demonstrated existing-code integrity fixes were kept: one pre-request pause
enforces the stricter configured/publisher delay, and the existing Portland layer
publisher refuses empty/duplicate membership or a reported transfer-limit breach.
Its interface is unchanged. Ten focused acquisition/atomic-publication checks
passed; these fixtures do not establish any Seattle source coverage.

The base GIS item's own description says it is **not an official zoning map**.
Its PDDL wording does not grant a blanket license to the separate lookup/overlay
items. Item modification times and GIS ordinance fields do not establish current
legal effect. The retained Legistar records identify CB 120969 / Ordinance 127219
and CB 120993 / Ordinance 127376, with distinct attachment/version labels; neither
detail page is treated as the attached ordinance text. Previously denied Seattle
pages and unavailable attachments were not retried.

A complete permitted source export, verified membership, actual joins and an
offline coordinate-to-evidence check are still required before claiming this
workflow. The next collection work instead expands the existing Florida edition.
