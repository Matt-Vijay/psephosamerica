"""Schema contract tests — no database required.

Reads db/schema.sql and db/migrations/0001_init.sql as text and asserts
that the required tables, columns, constraints, and indexes from
ENGINEERING_SPEC_V1.md §5 are present.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "db" / "schema.sql"
MIGRATION_PATH = ROOT / "db" / "migrations" / "0001_init.sql"
RUNTIME_DISCLOSURE_ARTIFACTS_PATH = ROOT / "src" / "runtime" / "disclosures_artifacts.py"

# ---------------------------------------------------------------------------
# Canonical tables from ENGINEERING_SPEC_V1.md §5
# ---------------------------------------------------------------------------

CORE_TABLES = [
    "member",
    "member_term",
    "committee",
    "committee_membership",
    "bill",
    "bill_sponsor",
    "vote_event",
    "vote_cast",
    "fec_committee",
    "fec_candidate_committee_linkage",
    "contribution",
    "financial_disclosure",
    "holding",
    "transaction",
    "rule_fire",
    "evidence_card",
    "score_snapshot",
    "ontology_edge",
]

PROVENANCE_TABLES = [
    "data_source",
    "ingestion_run",
    "source_artifact",
    "parse_run",
    "match_decision",
    "review_queue",
]

ALL_TABLES = CORE_TABLES + PROVENANCE_TABLES

# Canonical identifiers from §6
CANONICAL_ID_COLUMNS = {
    "member": ["bioguide_id", "lis_member_id", "fec_candidate_id"],
    "fec_committee": ["fec_committee_id"],
}

# Key columns that must exist per spec §9 (rule_fire requirements)
RULE_FIRE_REQUIRED_COLUMNS = [
    "rule_id",
    "dimension",
    "rule_version",
    "severity",
    "source_facts",
    "derived_values",
    "parameters",
    "conditions",
    "source_types_required",
    "explanation",
    "recompute_run_id",
    "subject_member_id",
]

# Evidence card required fields from §9
EVIDENCE_CARD_REQUIRED_COLUMNS = [
    "member_id",
    "dimension",
    "score_delta",
    "short_explanation",
    "source_anchors",
    "confidence_label",
    "facts",
    "inferences",
    "normative_judgments",
]

ONTOLOGY_EDGE_REQUIRED_COLUMNS = [
    "edge_id",
    "edge_type",
    "recompute_run_id",
    "subject_node_type",
    "subject_node_id",
    "object_node_type",
    "object_node_id",
    "source_anchors",
    "confidence",
    "attributes",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _lower(text: str) -> str:
    return text.lower()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_create_table(sql: str, table: str) -> None:
    # "transaction" is a reserved word and quoted in the DDL
    pattern_quoted = f'create table "{table}"'
    pattern_plain = f"create table {table}"
    lowered = _lower(sql)
    assert pattern_quoted in lowered or pattern_plain in lowered, (
        f"CREATE TABLE for '{table}' not found"
    )


def _assert_column_in_table(sql: str, table: str, column: str) -> None:
    lowered = _lower(sql)
    assert column in lowered, f"Column '{column}' not found in schema (expected in table '{table}')"


# ---------------------------------------------------------------------------
# Tests — schema.sql
# ---------------------------------------------------------------------------


class TestSchemaTables:
    def test_schema_file_exists(self) -> None:
        assert SCHEMA_PATH.exists(), f"{SCHEMA_PATH} does not exist"

    def test_all_tables_present(self) -> None:
        sql = _read(SCHEMA_PATH)
        for table in ALL_TABLES:
            _assert_create_table(sql, table)

    def test_canonical_id_columns(self) -> None:
        sql = _read(SCHEMA_PATH)
        for table, columns in CANONICAL_ID_COLUMNS.items():
            for col in columns:
                _assert_column_in_table(sql, table, col)

    def test_rule_fire_columns(self) -> None:
        sql = _read(SCHEMA_PATH)
        for col in RULE_FIRE_REQUIRED_COLUMNS:
            _assert_column_in_table(sql, "rule_fire", col)

    def test_rule_fire_source_record_id_is_unique_for_upsert(self) -> None:
        sql = _lower(_read(SCHEMA_PATH))
        assert "unique (source_record_id)" in sql

    def test_contribution_source_record_id_is_unique_for_fec_upsert(self) -> None:
        sql = _lower(_read(SCHEMA_PATH))
        contribution_section = sql.split("create table contribution", 1)[1].split(
            "create index idx_contribution_recipient",
            1,
        )[0]
        assert "unique (source_record_id)" in contribution_section

    def test_evidence_card_columns(self) -> None:
        sql = _read(SCHEMA_PATH)
        for col in EVIDENCE_CARD_REQUIRED_COLUMNS:
            _assert_column_in_table(sql, "evidence_card", col)

    def test_score_snapshot_has_manifest(self) -> None:
        sql = _read(SCHEMA_PATH)
        _assert_column_in_table(sql, "score_snapshot", "published_manifest_sha256")

    def test_ontology_edge_columns(self) -> None:
        sql = _read(SCHEMA_PATH)
        for col in ONTOLOGY_EDGE_REQUIRED_COLUMNS:
            _assert_column_in_table(sql, "ontology_edge", col)

    def test_ontology_edge_has_unique_edge_id_for_upsert(self) -> None:
        sql = _lower(_read(SCHEMA_PATH))
        assert "create table ontology_edge" in sql
        ontology_section = sql.split("create table ontology_edge", 1)[1]
        assert "unique (edge_id)" in ontology_section

    def test_confidence_labels(self) -> None:
        sql = _read(SCHEMA_PATH)
        for label in ("HIGH", "MEDIUM", "LOW"):
            assert label in sql, f"Confidence label '{label}' not found in schema"


class TestRuntimeRunTypes:
    def test_schema_allows_only_canonical_ingestion_run_types(self) -> None:
        sql = _lower(_read(SCHEMA_PATH))
        assert "run_type in ('ingest', 'recompute', 'export')" in sql

    def test_artifact_ingest_runtime_uses_ingest_stage_not_run_type(self) -> None:
        text = _read(RUNTIME_DISCLOSURE_ARTIFACTS_PATH)
        assert '"artifact_ingest"' in text
        assert (
            'start_ingestion_run(\n        conn,\n        data_source["id"],\n        "ingest",'
            in text
        )


# ---------------------------------------------------------------------------
# Tests — migration matches schema
# ---------------------------------------------------------------------------


class TestMigrationMatchesSchema:
    def test_migration_file_exists(self) -> None:
        assert MIGRATION_PATH.exists(), f"{MIGRATION_PATH} does not exist"

    def test_migration_has_all_tables(self) -> None:
        sql = _read(MIGRATION_PATH)
        for table in ALL_TABLES:
            _assert_create_table(sql, table)

    def test_migration_wrapped_in_transaction(self) -> None:
        sql = _read(MIGRATION_PATH)
        lowered = _lower(sql)
        assert "begin;" in lowered, "Migration must start with BEGIN;"
        assert "commit;" in lowered, "Migration must end with COMMIT;"

    def test_migration_has_indexes(self) -> None:
        sql = _read(MIGRATION_PATH)
        lowered = _lower(sql)
        assert "create index" in lowered, "Migration should include indexes"

    def test_schema_and_migration_table_parity(self) -> None:
        """Every CREATE TABLE in schema.sql should also appear in 0001_init.sql."""
        schema_sql = _lower(_read(SCHEMA_PATH))
        migration_sql = _lower(_read(MIGRATION_PATH))

        for table in ALL_TABLES:
            in_schema = (
                f'create table "{table}"' in schema_sql or f"create table {table}" in schema_sql
            )
            in_migration = (
                f'create table "{table}"' in migration_sql
                or f"create table {table}" in migration_sql
            )
            assert in_schema == in_migration, (
                f"Table '{table}' parity mismatch between schema.sql and 0001_init.sql"
            )
