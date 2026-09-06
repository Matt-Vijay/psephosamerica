"""Measured integrity report for the canonical legislative time machine."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.time_machine.catalog import connect
from src.time_machine.model import TABLE_KEYS, TABLE_SCHEMAS


def _scalar(connection: Any, sql: str, parameters: list[Any] | None = None) -> Any:
    row = connection.execute(sql, parameters or []).fetchone()
    return row[0] if row else None


def _key_expression(table: str) -> str:
    columns = TABLE_KEYS[table]
    if len(columns) == 1:
        return columns[0]
    encoded = ", ".join(f"coalesce(CAST({column} AS VARCHAR), '<NULL>')" for column in columns)
    return f"concat_ws(chr(31), {encoded})"


def _table_measure(connection: Any, table: str) -> dict[str, Any]:
    schema = TABLE_SCHEMAS[table]
    key = _key_expression(table)
    temporal = "available_at" in schema.names
    fact_temporal = "event_at" in schema.names
    selections = [
        "count(*) AS rows",
        f"count(DISTINCT {key}) AS distinct_keys",
        f"count(*) FILTER (WHERE {key} IS NULL) AS null_keys",
    ]
    if "source_url" in schema.names:
        selections.append(
            "count(*) FILTER (WHERE source_url IS NULL OR trim(source_url) = '') AS missing_urls"
        )
    if "content_sha256" in schema.names:
        selections.append(
            "count(*) FILTER (WHERE content_sha256 IS NULL OR "
            "NOT regexp_full_match(content_sha256, '[0-9a-f]{64}')) AS missing_or_bad_hashes"
        )
    if temporal:
        selections.extend(
            [
                "CAST(min(available_at) AS VARCHAR) AS available_min",
                "CAST(max(available_at) AS VARCHAR) AS available_max",
                "CAST(min(observed_at) AS VARCHAR) AS observed_min",
                "CAST(max(observed_at) AS VARCHAR) AS observed_max",
                "count(*) FILTER (WHERE available_at IS NULL) AS unknown_availability",
                "count(*) FILTER (WHERE available_at IS NOT NULL AND observed_at IS NOT NULL AND available_at > observed_at) AS observed_before_available",
            ]
        )
    if fact_temporal:
        selections.extend(
            [
                "CAST(min(event_at) AS VARCHAR) AS event_min",
                "CAST(max(event_at) AS VARCHAR) AS event_max",
                "count(*) FILTER (WHERE event_at IS NOT NULL AND available_at < event_at) AS preannounced_future_events",
                "count(*) FILTER (WHERE valid_from IS NOT NULL AND valid_to IS NOT NULL AND valid_to <= valid_from) AS invalid_validity",
            ]
        )
    cursor = connection.execute(f"SELECT {', '.join(selections)} FROM tm.{table}")  # nosec B608 - identifiers and expressions come from fixed TABLE_SCHEMAS
    names = [column[0] for column in cursor.description]
    values = cursor.fetchone()
    result = dict(zip(names, values, strict=True))
    result["duplicate_keys"] = int(result["rows"] or 0) - int(result["distinct_keys"] or 0)
    for name, value in list(result.items()):
        if isinstance(value, datetime):
            result[name] = value.isoformat().replace("+00:00", "Z")
    return result


def _source_families(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT source_family, count(*) AS artifacts, sum(byte_count) AS bytes,
               CAST(min(observed_at) AS VARCHAR) AS observed_min,
               CAST(max(observed_at) AS VARCHAR) AS observed_max
        FROM tm.source_artifacts GROUP BY source_family ORDER BY source_family
        """
    ).fetchall()
    return [
        {
            "source_family": family,
            "artifacts": artifacts,
            "bytes": byte_count,
            "observed_min": lo,
            "observed_max": hi,
        }
        for family, artifacts, byte_count, lo, hi in rows
    ]


def _examples(connection: Any, sql: str, *, limit: int = 20) -> list[str]:
    return [str(row[0]) for row in connection.execute(sql, [limit]).fetchall()]


def _openstates_manifest_audit(source_root: Path | None) -> dict[str, Any] | None:
    if source_root is None:
        return None
    manifest = source_root / "data/raw/openstates_bulk/_manifest.json"
    raw_dir = manifest.parent
    if not manifest.exists():
        return None
    rows = json.loads(manifest.read_text(encoding="utf-8"))
    labels: dict[str, list[str]] = {}
    for row in rows:
        labels.setdefault(str(row.get("text") or ""), []).append(str(row.get("href") or ""))
    collisions = {label: urls for label, urls in labels.items() if len(urls) > 1}
    return {
        "manifest_entries": len(rows),
        "local_zip_files": len(list(raw_dir.glob("*.zip"))),
        "manifest_minus_local": len(rows) - len(list(raw_dir.glob("*.zip"))),
        "duplicate_labels": collisions,
    }


def generate_integrity_report(
    output_root: Path,
    *,
    source_root: Path | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Compute exact key/source/temporal checks and text coverage."""
    connection = connect(output_root)
    try:
        tables = {table: _table_measure(connection, table) for table in TABLE_SCHEMAS}
        broken_artifact_fks: dict[str, int] = {}
        artifact_hash_mismatches: dict[str, int] = {}
        for table in TABLE_SCHEMAS:
            if table == "source_artifacts":
                continue
            broken_artifact_fks[table] = _scalar(
                connection,
                f"""
                SELECT count(*) FROM tm.{table} f
                LEFT JOIN tm.source_artifacts a USING (source_artifact_id)
                WHERE f.source_artifact_id IS NULL OR a.source_artifact_id IS NULL
                """,  # nosec B608 - identifiers and expressions come from fixed TABLE_SCHEMAS
            )
            artifact_hash_mismatches[table] = _scalar(
                connection,
                f"""
                SELECT count(*) FROM tm.{table} f
                JOIN tm.source_artifacts a USING (source_artifact_id)
                WHERE f.content_sha256 IS DISTINCT FROM a.content_sha256
                """,  # nosec B608 - identifiers and expressions come from fixed TABLE_SCHEMAS
            )
        broken_relations = {
            "terms_person": _scalar(
                connection,
                "SELECT count(*) FROM tm.terms x LEFT JOIN tm.people p USING(person_id) WHERE p.person_id IS NULL",
            ),
            "bills_session": _scalar(
                connection,
                "SELECT count(*) FROM tm.bills x LEFT JOIN tm.sessions s USING(session_id) WHERE s.session_id IS NULL",
            ),
            "text_bill": _scalar(
                connection,
                "SELECT count(*) FROM tm.bill_text_versions x LEFT JOIN tm.bills b USING(bill_id) WHERE b.bill_id IS NULL",
            ),
            "actions_bill": _scalar(
                connection,
                "SELECT count(*) FROM tm.actions x LEFT JOIN tm.bills b USING(bill_id) WHERE b.bill_id IS NULL",
            ),
            "roll_session": _scalar(
                connection,
                "SELECT count(*) FROM tm.roll_calls x LEFT JOIN tm.sessions s USING(session_id) WHERE s.session_id IS NULL",
            ),
            "votes_roll_call": _scalar(
                connection,
                "SELECT count(*) FROM tm.member_votes x LEFT JOIN tm.roll_calls r USING(roll_call_id) WHERE r.roll_call_id IS NULL",
            ),
        }
        unmatched = {
            "member_votes_without_person": _scalar(
                connection, "SELECT count(*) FROM tm.member_votes WHERE person_id IS NULL"
            ),
            "roll_calls_without_resolved_bill": _scalar(
                connection,
                "SELECT count(*) FROM tm.roll_calls WHERE source_bill_id IS NOT NULL AND bill_id IS NULL",
            ),
            "person_examples": _examples(
                connection,
                """
                SELECT DISTINCT coalesce(source_person_id, member_name)
                FROM tm.member_votes
                WHERE person_id IS NULL
                  AND coalesce(source_person_id, member_name) IS NOT NULL
                LIMIT ?
                """,
            ),
            "bill_examples": _examples(
                connection,
                "SELECT DISTINCT source_bill_id FROM tm.roll_calls WHERE source_bill_id IS NOT NULL AND bill_id IS NULL LIMIT ?",
            ),
        }
        text_coverage = {
            "billstatus_metadata_rows": _scalar(
                connection,
                "SELECT count(*) FROM tm.bills WHERE source_family = 'govinfo_billstatus'",
            ),
            "federal_bills": _scalar(
                connection, "SELECT count(*) FROM tm.bills WHERE jurisdiction_id = 'us-congress'"
            ),
            "federal_bills_with_true_text": _scalar(
                connection,
                """
                SELECT count(DISTINCT b.bill_id) FROM tm.bills b
                JOIN tm.bill_text_versions v USING(bill_id)
                WHERE b.jurisdiction_id = 'us-congress' AND v.is_full_text
                  AND length(trim(v.text_content)) > 0
                """,
            ),
            "true_federal_text_versions": _scalar(
                connection,
                """
                SELECT count(*) FROM tm.bill_text_versions v
                JOIN tm.bills b USING(bill_id)
                WHERE b.jurisdiction_id = 'us-congress' AND v.is_full_text
                  AND length(trim(v.text_content)) > 0
                """,
            ),
            "true_federal_text_characters": _scalar(
                connection,
                """
                SELECT coalesce(sum(length(v.text_content)), 0) FROM tm.bill_text_versions v
                JOIN tm.bills b USING(bill_id)
                WHERE b.jurisdiction_id = 'us-congress' AND v.is_full_text
                """,
            ),
            "state_version_links": _scalar(
                connection,
                "SELECT count(*) FROM tm.bill_text_versions WHERE source_family = 'openstates_bulk' AND NOT is_full_text",
            ),
            "false_full_text_rows": _scalar(
                connection,
                "SELECT count(*) FROM tm.bill_text_versions WHERE is_full_text AND (text_content IS NULL OR length(trim(text_content)) = 0)",
            ),
        }
        law_links = {
            "rows": _scalar(connection, "SELECT count(*) FROM tm.law_links"),
            "without_official_id": _scalar(
                connection,
                "SELECT count(*) FROM tm.law_links WHERE law_id IS NULL OR law_id NOT LIKE 'PLAW-%'",
            ),
        }
        sources = _source_families(connection)
    finally:
        connection.close()

    duplicate_total = sum(int(row.get("duplicate_keys") or 0) for row in tables.values())
    temporal_total = sum(
        int(row.get(name) or 0)
        for row in tables.values()
        for name in ("observed_before_available", "invalid_validity")
    )
    broken_total = sum(int(value or 0) for value in broken_artifact_fks.values()) + sum(
        int(value or 0) for value in broken_relations.values()
    )
    artifact_hash_mismatch_total = sum(
        int(value or 0) for value in artifact_hash_mismatches.values()
    )
    hard_failures = {
        "duplicate_keys": duplicate_total,
        "temporal_violations": temporal_total,
        "broken_foreign_keys": broken_total,
        "artifact_hash_mismatches": artifact_hash_mismatch_total,
        "false_full_text_rows": int(text_coverage["false_full_text_rows"] or 0),
        "undefended_law_links": int(law_links["without_official_id"] or 0),
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": "fail" if any(hard_failures.values()) else "pass",
        "hard_failures": hard_failures,
        "tables": tables,
        "source_families": sources,
        "unmatched_ids": unmatched,
        "broken_artifact_foreign_keys": broken_artifact_fks,
        "artifact_hash_mismatches": artifact_hash_mismatches,
        "broken_relations": broken_relations,
        "text_version_coverage": text_coverage,
        "law_links": law_links,
        "openstates_manifest": _openstates_manifest_audit(source_root),
        "known_limitations": [
            "The legacy House feed retains bill-linked roll calls only; procedural House rolls discarded upstream cannot be reconstructed.",
            "BILLSTATUS title, subjects and CRS summaries are metadata, never full bill text.",
            "The retained BILLSTATUS dossiers omit federal actions and amendments; V1 does not reconstruct them from votes or prose.",
            "State version rows are official links unless is_full_text is true; linked documents were not downloaded.",
            "Source-reported state dates are preserved, including obvious year outliers; no guessed corrections are applied.",
        ],
    }
    if write:
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "integrity.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
        )
        (output_root / "integrity.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# American Legislative Time Machine — integrity report",
        "",
        f"Status: **{str(report['status']).upper()}**  ",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Canonical tables",
        "",
        "| table | rows | duplicate keys | event range | availability range | observation range |",
        "|---|---:|---:|---|---|---|",
    ]
    for name, row in report["tables"].items():
        lines.append(
            f"| {name} | {row['rows']:,} | {row['duplicate_keys']:,} | "
            f"{row.get('event_min') or '—'} … {row.get('event_max') or '—'} | "
            f"{row.get('available_min') or '—'} … {row.get('available_max') or '—'} | "
            f"{row.get('observed_min') or '—'} … {row.get('observed_max') or '—'} |"
        )
    lines.extend(
        ["", "## Source families", "", "| family | artifacts | bytes |", "|---|---:|---:|"]
    )
    for row in report["source_families"]:
        lines.append(f"| {row['source_family']} | {row['artifacts']:,} | {row['bytes']:,} |")
    coverage = report["text_version_coverage"]
    lines.extend(
        [
            "",
            "## Text truth",
            "",
            f"- BILLSTATUS metadata rows: {coverage['billstatus_metadata_rows']:,}",
            f"- Federal bills with genuine BILLS text: {coverage['federal_bills_with_true_text']:,} / {coverage['federal_bills']:,}",
            f"- Genuine federal text versions: {coverage['true_federal_text_versions']:,}",
            f"- State document/version links (not downloaded text): {coverage['state_version_links']:,}",
            "",
            "## Hard invariants",
            "",
        ]
    )
    for name, value in report["hard_failures"].items():
        lines.append(f"- {name.replace('_', ' ')}: {value:,}")
    missing_urls = sum(int(row.get("missing_urls") or 0) for row in report["tables"].values())
    bad_hashes = sum(
        int(row.get("missing_or_bad_hashes") or 0) for row in report["tables"].values()
    )
    unmatched = report["unmatched_ids"]
    future_events = sum(
        int(row.get("preannounced_future_events") or 0) for row in report["tables"].values()
    )
    lines.extend(
        [
            "",
            "## Source and identity gaps",
            "",
            f"- Rows with a missing source URL: {missing_urls:,}",
            f"- Rows with a missing or malformed retained-artifact hash: {bad_hashes:,}",
            f"- Member-vote rows without an exact person ID: {unmatched['member_votes_without_person']:,}",
            f"- Roll calls with a source bill ID that did not resolve: {unmatched['roll_calls_without_resolved_bill']:,}",
            f"- Unresolved person/name examples: {', '.join(unmatched['person_examples']) or '—'}",
            f"- Unresolved bill examples: {', '.join(unmatched['bill_examples']) or '—'}",
            "",
            "## Temporal observations",
            "",
            f"- Source rows whose availability precedes a future event time: {future_events:,}",
            "- These are reported, not rewritten; `as_of` still excludes each row until its event time.",
        ]
    )
    manifest = report.get("openstates_manifest")
    if manifest is not None:
        lines.extend(
            [
                "",
                "## OpenStates inventory",
                "",
                f"- Manifest entries: {manifest['manifest_entries']:,}",
                f"- Local ZIP files: {manifest['local_zip_files']:,}",
                f"- Manifest minus local: {manifest['manifest_minus_local']:,}",
                f"- Duplicate labels: {len(manifest['duplicate_labels']):,}",
            ]
        )
    lines.extend(["", "## Known limitations", ""])
    lines.extend(f"- {item}" for item in report["known_limitations"])
    return "\n".join(lines) + "\n"


__all__ = ["generate_integrity_report"]
