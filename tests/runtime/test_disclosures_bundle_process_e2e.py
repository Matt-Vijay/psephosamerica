"""End-to-end tests for src/runtime/disclosures_bundle_process.py.

These tests prove the real staged-artifact path by using:
  - A DisclosuresBundle built from inline JSON (no mocks).
  - Temp artifact files on disk with content that matches the bundle SHA-256.
  - The auto-build parse inputs path (step 3): parse_inputs is NOT supplied
    explicitly; _build_parse_inputs reads bytes from disk and verifies SHA-256.
  - A real _BundleIndexProvider resolving index rows from the bundle.
  - A real transform_parse_sessions invocation (sessions without a ParseResult
    are skipped; transform logic is still exercised fully).
  - Minimal patching of irreducible DB/write boundaries only:
      stage_disclosures_bundle, run_parse_session, run_disclosures_load_runtime,
      fetch_member_rows_for_disclosures.

No network calls.  No sys.modules injection.  All temp files generated from
inline content.

Flow under test: validate → stage → parse inputs → parse → transform → load
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.runtime.disclosures_bundle import (
    DisclosuresBundle,
    HouseBundledIndexRow,
    SenateBundledIndexRow,
    disclosures_bundle_from_dict,
)
from src.runtime.disclosures_bundle_files import Sha256Mismatch
from src.runtime.disclosures_bundle_process import (
    DisclosuresBundleProcessResult,
    _build_parse_inputs,
    _BundleIndexProvider,
    run_disclosures_bundle_process,
)
from src.runtime.disclosures_stage import DisclosureStagingResult
from src.runtime.parse_runs import ParseSessionResult
from tests.support.disclosures_bundle_fixtures import (
    build_house_senate_fixture,
    make_house_ptr_text,
    make_senate_annual_text,
)

# ---------------------------------------------------------------------------
# Irreducible DB/write boundary patch targets
# ---------------------------------------------------------------------------

_STAGE = "src.runtime.disclosures_bundle_process.stage_disclosures_bundle"
_PARSE_SESSION = "src.runtime.disclosures_parse.run_parse_session"
_LOAD = "src.runtime.disclosures_bundle_process.run_disclosures_load_runtime"
_FETCH_MEMBERS = "src.runtime.disclosures_bundle_process.fetch_member_rows_for_disclosures"
_LOAD_UNPARSED = "src.runtime.disclosures_parse.load_unparsed_disclosure_artifacts"

# ---------------------------------------------------------------------------
# Canonical inline fixture data — generated from deterministic stub bytes
# ---------------------------------------------------------------------------

_STUB_BYTES = b"%PDF-1.4 psephosamerica-e2e-stub"
_SHA256 = hashlib.sha256(_STUB_BYTES).hexdigest()

_HOUSE_SOURCE_RECORD_ID = "12345"
_HOUSE_STORAGE_URI = "house/2024/12345.pdf"
_SENATE_SOURCE_RECORD_ID = "uuid-xyz"
_SENATE_STORAGE_URI = "senate/2023/uuid-xyz.pdf"


def _house_bundle_dict(sha256: str = _SHA256) -> dict:
    return {
        "artifacts": [
            {
                "source_record_id": _HOUSE_SOURCE_RECORD_ID,
                "chamber": "house",
                "filing_year": 2024,
                "storage_uri": _HOUSE_STORAGE_URI,
                "source_url": (
                    "https://disclosures.house.gov/public_disc/financial-pdfs/2024/12345.pdf"
                ),
                "source_slug": "house_disclosures",
                "artifact_kind": "pdf",
                "sha256": sha256,
                "index_row": {
                    "last_name": "Smith",
                    "first_name": "John",
                    "suffix": "",
                    "raw_filing_type": "O",
                    "state_dst": "CA08",
                    "filing_date": "2024-01-15",
                    "doc_id": _HOUSE_SOURCE_RECORD_ID,
                    "filing_kind": "annual",
                },
            }
        ]
    }


def _senate_bundle_dict(sha256: str = _SHA256) -> dict:
    return {
        "artifacts": [
            {
                "source_record_id": _SENATE_SOURCE_RECORD_ID,
                "chamber": "senate",
                "filing_year": 2023,
                "storage_uri": _SENATE_STORAGE_URI,
                "source_url": ("https://efdsearch.senate.gov/search/view/paper/uuid-xyz/"),
                "source_slug": "senate_disclosures",
                "artifact_kind": "pdf",
                "sha256": sha256,
                "index_row": {
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "office": "Senator, TX",
                    "report_type": "Annual Report for CY2023",
                    "date_filed": "01/15/2024",
                    "doc_id": _SENATE_SOURCE_RECORD_ID,
                    "filing_year": 2023,
                },
            }
        ]
    }


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _write_artifact(local_root: Path, storage_uri: str, data: bytes) -> None:
    dest = local_root / storage_uri
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _house_artifact_row(artifact_id: int = 1) -> dict:
    return {
        "id": artifact_id,
        "chamber": "house",
        "filing_year": 2024,
        "source_record_id": _HOUSE_SOURCE_RECORD_ID,
        "storage_uri": _HOUSE_STORAGE_URI,
    }


def _senate_artifact_row(artifact_id: int = 2) -> dict:
    return {
        "id": artifact_id,
        "chamber": "senate",
        "filing_year": 2023,
        "source_record_id": _SENATE_SOURCE_RECORD_ID,
        "storage_uri": _SENATE_STORAGE_URI,
    }


def _staged_result(artifact_row: dict | None = None) -> DisclosureStagingResult:
    row = artifact_row or _house_artifact_row()
    return DisclosureStagingResult(
        data_source={"id": 1, "slug": row.get("chamber", "house") + "_disclosures"},
        run_id=10,
        artifact_rows=(row,),
        staged_count=1,
        mirrored_count=0,
    )


def _parse_session(run_id: int = 1) -> ParseSessionResult:
    """A session whose parse_result carries no ParseResult — transform skips it."""
    return ParseSessionResult(
        run_id=run_id,
        parse_result={
            "page_count": 0,
            "ocr_page_count": 0,
            "confidence_summary": {},
            "parsed_document": None,
            "resolved_member": None,
            "source_artifact_id": run_id,
            "source_record_id": _HOUSE_SOURCE_RECORD_ID,
            "chamber": "house",
        },
    )


def _load_result() -> MagicMock:
    r = MagicMock()
    r.run_id = 99
    return r


class _E2EPatchContext:
    """Context manager that patches the four irreducible DB/write boundaries.

    stage_result  — DisclosureStagingResult returned by stage_disclosures_bundle.
    session       — ParseSessionResult returned by run_parse_session (per call).
    load_result   — mock returned by run_disclosures_load_runtime.
    member_rows   — list returned by fetch_member_rows_for_disclosures.

    parse_inputs is NOT patched here; the auto-build step (_build_parse_inputs)
    runs for real so SHA-256 is verified against on-disk content.
    """

    def __init__(
        self,
        stage_result: DisclosureStagingResult | None = None,
        session: ParseSessionResult | None = None,
        load_result: object | None = None,
        member_rows: list | None = None,
    ) -> None:
        self._sr = stage_result or _staged_result()
        self._sess = session or _parse_session()
        self._lr = load_result or _load_result()
        self._mr = member_rows if member_rows is not None else []
        self.mocks: dict = {}

    def __enter__(self):
        self._p_stage = patch(_STAGE, return_value=self._sr)
        self._p_parse = patch(_PARSE_SESSION, return_value=self._sess)
        self._p_load = patch(_LOAD, return_value=self._lr)
        self._p_members = patch(_FETCH_MEMBERS, return_value=self._mr)
        self.mocks["stage"] = self._p_stage.__enter__()
        self.mocks["parse_session"] = self._p_parse.__enter__()
        self.mocks["load"] = self._p_load.__enter__()
        self.mocks["members"] = self._p_members.__enter__()
        return self.mocks

    def __exit__(self, *exc):
        self._p_members.__exit__(*exc)
        self._p_load.__exit__(*exc)
        self._p_parse.__exit__(*exc)
        self._p_stage.__exit__(*exc)


# ---------------------------------------------------------------------------
# _BundleIndexProvider with real bundle data
# ---------------------------------------------------------------------------


class TestBundleIndexProviderE2E:
    """Verify _BundleIndexProvider behaviour against a real DisclosuresBundle.

    No mocks — the bundle is built from inline JSON, the provider is
    constructed directly, and load_matches is called with typed artifact rows.
    """

    def test_house_entry_resolves_correct_index_row(self):
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        provider = _BundleIndexProvider(bundle)
        row = _house_artifact_row()

        matches = provider.load_matches([row])

        assert len(matches) == 1
        assert matches[0].artifact is row
        assert matches[0].index_row is not None

    def test_house_index_row_carries_correct_fields(self):
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        provider = _BundleIndexProvider(bundle)

        matches = provider.load_matches([_house_artifact_row()])
        index_row = matches[0].index_row

        assert isinstance(index_row, HouseBundledIndexRow)
        assert index_row.last_name == "Smith"
        assert index_row.first_name == "John"
        assert index_row.state_dst == "CA08"
        assert index_row.doc_id == _HOUSE_SOURCE_RECORD_ID
        assert index_row.filing_kind == "annual"

    def test_senate_entry_resolves_correct_index_row(self):
        bundle = disclosures_bundle_from_dict(_senate_bundle_dict())
        provider = _BundleIndexProvider(bundle)
        row = _senate_artifact_row()

        matches = provider.load_matches([row])

        assert len(matches) == 1
        assert matches[0].index_row is not None

    def test_senate_index_row_carries_correct_fields(self):
        bundle = disclosures_bundle_from_dict(_senate_bundle_dict())
        provider = _BundleIndexProvider(bundle)

        matches = provider.load_matches([_senate_artifact_row()])
        index_row = matches[0].index_row

        assert isinstance(index_row, SenateBundledIndexRow)
        assert index_row.last_name == "Doe"
        assert index_row.office == "Senator, TX"
        assert index_row.doc_id == _SENATE_SOURCE_RECORD_ID

    def test_unmatched_source_record_id_returns_none_index_row(self):
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        provider = _BundleIndexProvider(bundle)
        row = {
            "id": 99,
            "chamber": "house",
            "filing_year": 2024,
            "source_record_id": "XXXXXX",
        }

        matches = provider.load_matches([row])

        assert matches[0].index_row is None

    def test_multiple_artifact_rows_all_resolved(self):
        bundle = disclosures_bundle_from_dict(
            {
                "artifacts": [
                    _house_bundle_dict()["artifacts"][0],
                    _senate_bundle_dict()["artifacts"][0],
                ]
            }
        )
        provider = _BundleIndexProvider(bundle)
        rows = [_house_artifact_row(artifact_id=1), _senate_artifact_row(artifact_id=2)]

        matches = provider.load_matches(rows)

        assert len(matches) == 2
        assert matches[0].index_row is not None
        assert matches[1].index_row is not None

    def test_preserves_artifact_reference_order(self):
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        provider = _BundleIndexProvider(bundle)
        rows = [_house_artifact_row(artifact_id=i) for i in range(1, 4)]
        extra_rows = [
            {"id": 10, "chamber": "house", "filing_year": 2024, "source_record_id": "X"},
            {"id": 11, "chamber": "house", "filing_year": 2024, "source_record_id": "Y"},
        ]
        all_rows = [rows[0]] + extra_rows

        matches = provider.load_matches(all_rows)

        assert len(matches) == 3
        assert matches[0].artifact is all_rows[0]
        assert matches[1].artifact is all_rows[1]
        assert matches[2].artifact is all_rows[2]


# ---------------------------------------------------------------------------
# Auto-build parse inputs: SHA-256 verification is real (not patched)
# ---------------------------------------------------------------------------


class TestBundleProcessSha256E2E:
    """Prove _build_parse_inputs runs real SHA-256 verification against on-disk bytes.

    verify_entry_sha256 / read_and_verify_entry are NOT patched here.
    """

    def test_matching_sha256_does_not_raise(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict(sha256=_SHA256))
        conn = MagicMock()

        with _E2EPatchContext(
            stage_result=_staged_result(_house_artifact_row()),
        ):
            result = run_disclosures_bundle_process(
                conn,
                bundle,
                local_root=local_root,
            )

        assert isinstance(result, DisclosuresBundleProcessResult)

    def test_corrupted_file_raises_sha256_mismatch(self, tmp_path):
        local_root = tmp_path / "artifacts"
        corrupted = b"this is not the original content"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, corrupted)
        # Bundle declares sha256 of _STUB_BYTES — file has wrong content.
        bundle = disclosures_bundle_from_dict(_house_bundle_dict(sha256=_SHA256))
        conn = MagicMock()

        with _E2EPatchContext():
            with pytest.raises(Sha256Mismatch):
                run_disclosures_bundle_process(
                    conn,
                    bundle,
                    local_root=local_root,
                )

    def test_sha256_verified_before_parse_session_called(self, tmp_path):
        """SHA-256 check (inside _build_parse_inputs) must abort before any parse_session write."""
        local_root = tmp_path / "artifacts"
        corrupted = b"wrong bytes"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, corrupted)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict(sha256=_SHA256))
        conn = MagicMock()

        with _E2EPatchContext() as mocks:
            with pytest.raises(Sha256Mismatch):
                run_disclosures_bundle_process(
                    conn,
                    bundle,
                    local_root=local_root,
                )

        # parse_session must not have been called — sha256 aborted first
        mocks["parse_session"].assert_not_called()

    def test_absolute_bundle_storage_uri_rejected(self, tmp_path):
        """Bundle storage paths stay confined to the artifact root."""
        artifact_path = tmp_path / "house" / "2024" / "12345.pdf"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(_STUB_BYTES)

        bundle_dict = _house_bundle_dict()
        bundle_dict["artifacts"][0]["storage_uri"] = str(artifact_path)

        with pytest.raises(ValueError, match="storage_uri"):
            disclosures_bundle_from_dict(bundle_dict)

    def test_realistic_text_payload_sha256_passes(self, tmp_path):
        """Realistic text content (not a short stub) passes real SHA-256 verification."""
        local_root = tmp_path / "artifacts"
        content = make_house_ptr_text(
            _HOUSE_SOURCE_RECORD_ID,
            last_name="Smith",
            first_name="John",
        )
        sha256 = hashlib.sha256(content).hexdigest()
        _write_artifact(local_root, _HOUSE_STORAGE_URI, content)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict(sha256=sha256))
        conn = MagicMock()

        with _E2EPatchContext(stage_result=_staged_result(_house_artifact_row())):
            result = run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        assert isinstance(result, DisclosuresBundleProcessResult)


# ---------------------------------------------------------------------------
# Auto-build parse inputs: verified bytes reach parse_session
# ---------------------------------------------------------------------------


class TestBundleProcessAutoParseInputsE2E:
    """Prove _build_parse_inputs constructs the inputs that reach run_parse_session."""

    def test_parse_session_called_once_for_one_artifact(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext(stage_result=_staged_result(_house_artifact_row())) as mocks:
            run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        mocks["parse_session"].assert_called_once()

    def test_parse_session_receives_correct_source_artifact_id(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        artifact_row = _house_artifact_row(artifact_id=42)
        conn = MagicMock()

        with _E2EPatchContext(stage_result=_staged_result(artifact_row)) as mocks:
            run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        _, parse_kwargs = mocks["parse_session"].call_args
        assert parse_kwargs["source_artifact_id"] == 42

    def test_local_root_none_rejects_relative_bundle_storage_uri(self):
        """Without local_root, relative bundle storage paths must fail truthfully."""
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext():
            with pytest.raises(ValueError, match="local_root is required"):
                run_disclosures_bundle_process(conn, bundle, local_root=None)

    def test_bundle_index_provider_resolves_match_in_auto_build_path(self, tmp_path):
        """The _BundleIndexProvider built inside the pipeline resolves the
        house entry's index_row for the auto-built parse input artifact row.

        We verify this by spying on _BundleIndexProvider.load_matches via
        the real class — no mock replaces it.
        """
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        artifact_row = _house_artifact_row(artifact_id=7)
        captured_matches = []

        original_init = _BundleIndexProvider.__init__

        def _spy_init(self_inner, b):
            original_init(self_inner, b)
            original_load = self_inner.load_matches

            def _spy_load(artifacts):
                result_inner = original_load(artifacts)
                captured_matches.extend(result_inner)
                return result_inner

            self_inner.load_matches = _spy_load

        conn = MagicMock()
        with patch.object(_BundleIndexProvider, "__init__", _spy_init):
            with _E2EPatchContext(stage_result=_staged_result(artifact_row)):
                run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        assert len(captured_matches) == 1
        assert captured_matches[0].index_row is not None
        assert isinstance(captured_matches[0].index_row, HouseBundledIndexRow)
        assert captured_matches[0].index_row.last_name == "Smith"

    def test_auto_built_parse_input_carries_correct_bytes(self, tmp_path):
        """The bytes in the auto-built DisclosureParseInput match the file content."""
        local_root = tmp_path / "artifacts"
        content = make_house_ptr_text(_HOUSE_SOURCE_RECORD_ID)
        sha256 = hashlib.sha256(content).hexdigest()
        _write_artifact(local_root, _HOUSE_STORAGE_URI, content)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict(sha256=sha256))

        # Use _build_parse_inputs directly to inspect the bytes.
        staged = _staged_result(_house_artifact_row())
        inputs = _build_parse_inputs(bundle, staged, local_root)

        assert len(inputs) == 1
        assert inputs[0].local_bytes == content


# ---------------------------------------------------------------------------
# Stage receives real DisclosuresBundle
# ---------------------------------------------------------------------------


class TestBundleProcessStageE2E:
    """Verify stage_disclosures_bundle is called with a real DisclosuresBundle."""

    def test_stage_receives_real_disclosures_bundle_instance(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext() as mocks:
            run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        staged_bundle = mocks["stage"].call_args[0][1]
        assert isinstance(staged_bundle, DisclosuresBundle)

    def test_stage_receives_bundle_with_correct_entry_count(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext() as mocks:
            run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        staged_bundle = mocks["stage"].call_args[0][1]
        assert len(staged_bundle.artifacts) == 1
        assert staged_bundle.artifacts[0].source_record_id == _HOUSE_SOURCE_RECORD_ID

    def test_stage_receives_bundle_with_correct_sha256(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext() as mocks:
            run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        staged_bundle = mocks["stage"].call_args[0][1]
        assert staged_bundle.artifacts[0].sha256 == _SHA256

    def test_stage_receives_local_root(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext() as mocks:
            run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        _, stage_kwargs = mocks["stage"].call_args
        assert stage_kwargs["local_root"] == local_root


# ---------------------------------------------------------------------------
# Transform runs real logic (not patched)
# ---------------------------------------------------------------------------


class TestBundleProcessTransformE2E:
    """Prove transform_parse_sessions is invoked with real parse sessions.

    transform_parse_sessions is NOT patched here.  Since stub parse sessions
    carry no ParseResult instance, all sessions are skipped by the transform
    and transform_count is zero.  The transform code path is still exercised.
    """

    def test_transform_count_zero_for_sessions_without_parsed_document(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext(
            session=_parse_session(),
            stage_result=_staged_result(_house_artifact_row()),
        ):
            result = run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        assert result.transform_count == 0

    def test_skipped_sessions_populated_when_transform_skips(self, tmp_path):
        """Sessions without a ParseResult are tracked as skipped with reason codes."""
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext(
            session=_parse_session(),
            stage_result=_staged_result(_house_artifact_row()),
        ):
            result = run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        assert result.skipped_transform_count == 1
        assert len(result.skipped_sessions) == 1
        assert result.skipped_sessions[0].reason_code == "no_parsed_document"

    def test_skipped_plus_transform_equals_parse_succeeded(self, tmp_path):
        """skipped_transform_count + transform_count == parse_result.succeeded_count."""
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext(
            session=_parse_session(),
            stage_result=_staged_result(_house_artifact_row()),
        ):
            result = run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        total = result.transform_count + result.skipped_transform_count
        assert total == result.parse_result.succeeded_count

    def test_parse_result_succeeded_count_matches_session_count(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext(
            session=_parse_session(run_id=5),
            stage_result=_staged_result(_house_artifact_row()),
        ):
            result = run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        assert result.parse_result.succeeded_count == 1
        assert result.parse_result.failed_count == 0

    def test_load_receives_empty_transformed_list_when_all_skipped(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext(stage_result=_staged_result(_house_artifact_row())) as mocks:
            run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        transformed_list = mocks["load"].call_args[0][1]
        assert transformed_list == []

    def test_result_is_typed_disclosures_bundle_process_result(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()
        lr = _load_result()

        with _E2EPatchContext(load_result=lr, stage_result=_staged_result(_house_artifact_row())):
            result = run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        assert isinstance(result, DisclosuresBundleProcessResult)
        assert result.load_result is lr

    def test_stage_result_propagated_to_pipeline_result(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        sr = _staged_result(_house_artifact_row())
        conn = MagicMock()

        with _E2EPatchContext(stage_result=sr):
            result = run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        assert result.stage_result is sr


# ---------------------------------------------------------------------------
# Senate bundle full path
# ---------------------------------------------------------------------------


class TestBundleProcessSenateE2E:
    """Prove the senate chamber path through the bundle process."""

    def test_senate_bundle_pipeline_runs_without_error(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _SENATE_STORAGE_URI, _STUB_BYTES)
        senate_sha = hashlib.sha256(_STUB_BYTES).hexdigest()
        bundle = disclosures_bundle_from_dict(_senate_bundle_dict(sha256=senate_sha))
        conn = MagicMock()

        senate_row = _senate_artifact_row()
        senate_stage = DisclosureStagingResult(
            data_source={"id": 2, "slug": "senate_disclosures"},
            run_id=20,
            artifact_rows=(senate_row,),
            staged_count=1,
            mirrored_count=0,
        )

        with _E2EPatchContext(
            stage_result=senate_stage,
            session=ParseSessionResult(
                run_id=2,
                parse_result={
                    "page_count": 0,
                    "ocr_page_count": 0,
                    "confidence_summary": {},
                    "parsed_document": None,
                    "resolved_member": None,
                    "source_artifact_id": 2,
                    "source_record_id": _SENATE_SOURCE_RECORD_ID,
                    "chamber": "senate",
                },
            ),
        ):
            result = run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        assert isinstance(result, DisclosuresBundleProcessResult)
        assert result.stage_result is senate_stage

    def test_senate_bundle_index_provider_resolves_entry(self):
        bundle = disclosures_bundle_from_dict(_senate_bundle_dict())
        provider = _BundleIndexProvider(bundle)
        row = _senate_artifact_row()

        matches = provider.load_matches([row])

        assert matches[0].index_row is not None
        assert isinstance(matches[0].index_row, SenateBundledIndexRow)
        assert matches[0].index_row.office == "Senator, TX"

    def test_senate_realistic_text_sha256_passes(self, tmp_path):
        """Senate annual text payload SHA-256 verification passes end-to-end."""
        local_root = tmp_path / "artifacts"
        content = make_senate_annual_text(
            _SENATE_SOURCE_RECORD_ID,
            last_name="Doe",
            first_name="Jane",
            office="Senator, TX",
        )
        sha256 = hashlib.sha256(content).hexdigest()
        _write_artifact(local_root, _SENATE_STORAGE_URI, content)
        bundle = disclosures_bundle_from_dict(_senate_bundle_dict(sha256=sha256))
        senate_row = _senate_artifact_row()
        senate_stage = DisclosureStagingResult(
            data_source={"id": 2, "slug": "senate_disclosures"},
            run_id=20,
            artifact_rows=(senate_row,),
            staged_count=1,
            mirrored_count=0,
        )
        conn = MagicMock()

        with _E2EPatchContext(stage_result=senate_stage):
            result = run_disclosures_bundle_process(conn, bundle, local_root=local_root)

        assert isinstance(result, DisclosuresBundleProcessResult)
        assert result.parse_result.succeeded_count == 1


# ---------------------------------------------------------------------------
# Multi-artifact fixture (build_house_senate_fixture)
# ---------------------------------------------------------------------------


class TestMultiArtifactFixtureE2E:
    """Prove the two-artifact House+Senate path using build_house_senate_fixture."""

    def test_two_artifact_fixture_passes_sha256(self, tmp_path):
        fixture = build_house_senate_fixture(tmp_path)
        bundle_json = fixture.bundle_json_path
        from src.runtime.disclosures_bundle import load_disclosures_bundle

        bundle = load_disclosures_bundle(bundle_json)

        assert len(bundle.artifacts) == 2

        # Verify both SHA-256 digests match actual file content.
        for entry in bundle.artifacts:
            file_data = fixture.artifact_paths[entry.source_record_id].read_bytes()
            actual_sha256 = hashlib.sha256(file_data).hexdigest()
            assert actual_sha256 == entry.sha256

    def test_two_artifact_fixture_index_rows_typed(self, tmp_path):
        fixture = build_house_senate_fixture(tmp_path)
        from src.runtime.disclosures_bundle import load_disclosures_bundle

        bundle = load_disclosures_bundle(fixture.bundle_json_path)

        house_entry = next(e for e in bundle.artifacts if e.chamber == "house")
        senate_entry = next(e for e in bundle.artifacts if e.chamber == "senate")
        assert isinstance(house_entry.index_row, HouseBundledIndexRow)
        assert isinstance(senate_entry.index_row, SenateBundledIndexRow)

    def test_build_parse_inputs_from_two_artifact_fixture(self, tmp_path):
        """Both artifacts auto-built from the fixture pass real SHA-256 checks."""
        fixture = build_house_senate_fixture(tmp_path)
        from src.runtime.disclosures_bundle import load_disclosures_bundle

        bundle = load_disclosures_bundle(fixture.bundle_json_path)

        # Simulate staged rows for both artifacts.
        house_row = {
            "id": 1,
            "chamber": "house",
            "filing_year": 2024,
            "source_record_id": "12345",
            "storage_uri": "house/2024/12345.pdf",
        }
        senate_row = {
            "id": 2,
            "chamber": "senate",
            "filing_year": 2023,
            "source_record_id": "uuid-xyz",
            "storage_uri": "senate/2023/uuid-xyz.pdf",
        }
        sr = DisclosureStagingResult(
            data_source={"id": 1, "slug": "house_disclosures"},
            run_id=5,
            artifact_rows=(house_row, senate_row),
            staged_count=2,
            mirrored_count=0,
        )

        inputs = _build_parse_inputs(bundle, sr, fixture.local_root)

        assert len(inputs) == 2
        chambers = {inp.chamber for inp in inputs}
        assert chambers == {"house", "senate"}

    def test_fixture_sha256s_attribute_populated(self, tmp_path):
        fixture = build_house_senate_fixture(tmp_path)
        assert len(fixture.sha256s) == 2
        for src_id, sha256 in fixture.sha256s.items():
            assert len(sha256) == 64
            file_data = fixture.artifact_paths[src_id].read_bytes()
            assert hashlib.sha256(file_data).hexdigest() == sha256
