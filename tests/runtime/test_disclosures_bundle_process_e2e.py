"""End-to-end tests for src/runtime/disclosures_bundle_process.py.

These tests prove the real staged-artifact path by using:
  - A DisclosuresBundle built from inline JSON (no mocks).
  - Temp PDF files on disk with correct SHA-256 digests verified for real.
  - Explicit DisclosureParseInput objects that bypass the DB artifact query.
  - A real _BundleIndexProvider resolving index rows from the bundle.
  - A real transform_parse_sessions invocation (all sessions skipped when
    parse_result contains no ParseResult — that is the expected outcome for
    stub bytes; the transform logic is still exercised fully).
  - Minimal patching of irreducible DB/write boundaries only:
      stage_disclosures_bundle, run_parse_session, run_disclosures_load_runtime,
      fetch_member_rows_for_disclosures.

No network calls.  No sys.modules injection.
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
    _BundleIndexProvider,
    run_disclosures_bundle_process,
)
from src.runtime.disclosures_parse_inputs import DisclosureParseInput
from src.runtime.disclosures_stage import DisclosureStagingResult
from src.runtime.parse_runs import ParseSessionResult

# ---------------------------------------------------------------------------
# Irreducible DB/write boundary patch targets
# ---------------------------------------------------------------------------

_STAGE = "src.runtime.disclosures_bundle_process.stage_disclosures_bundle"
_PARSE_SESSION = "src.runtime.disclosures_parse.run_parse_session"
_LOAD = "src.runtime.disclosures_bundle_process.run_disclosures_load_runtime"
_FETCH_MEMBERS = "src.runtime.disclosures_bundle_process.fetch_member_rows_for_disclosures"

# ---------------------------------------------------------------------------
# Canonical inline fixture data — generated from deterministic stub bytes
# ---------------------------------------------------------------------------

_STUB_BYTES = b"%PDF-1.4 openpact-e2e-stub"
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
                    "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/12345.pdf"
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
                "source_url": (
                    "https://efdsearch.senate.gov/search/view/paper/uuid-xyz/"
                ),
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


def _house_parse_input(artifact_row: dict | None = None) -> DisclosureParseInput:
    return DisclosureParseInput(
        artifact_row=artifact_row or _house_artifact_row(),
        local_bytes=_STUB_BYTES,
        chamber="house",
        source_record_id=_HOUSE_SOURCE_RECORD_ID,
    )


def _senate_parse_input(artifact_row: dict | None = None) -> DisclosureParseInput:
    return DisclosureParseInput(
        artifact_row=artifact_row or _senate_artifact_row(),
        local_bytes=_STUB_BYTES,
        chamber="senate",
        source_record_id=_SENATE_SOURCE_RECORD_ID,
    )


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
    """A session whose parse_result dict has no ParseResult — transform skips it."""
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
        # None of these extra rows match the single bundle entry.
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
# SHA-256 verification is real (not patched)
# ---------------------------------------------------------------------------


class TestBundleProcessSha256E2E:
    """Prove verify_entry_sha256 runs against real on-disk bytes.

    verify_entry_sha256 is NOT patched in this class.
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
                parse_inputs=[_house_parse_input()],
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
                    parse_inputs=[_house_parse_input()],
                )

    def test_sha256_verified_before_parse_session_called(self, tmp_path):
        """SHA-256 check must complete before any parse_session DB write."""
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
                    parse_inputs=[_house_parse_input()],
                )

        # parse_session must not have been called — sha256 aborted first
        mocks["parse_session"].assert_not_called()

    def test_no_sha256_check_when_local_root_is_none(self):
        """When local_root is None the SHA-256 step is skipped entirely."""
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        # No local file exists — would raise FileNotFoundError if checked.
        with _E2EPatchContext():
            result = run_disclosures_bundle_process(
                conn,
                bundle,
                local_root=None,
                parse_inputs=[_house_parse_input()],
            )

        assert isinstance(result, DisclosuresBundleProcessResult)


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
            run_disclosures_bundle_process(
                conn, bundle, local_root=local_root, parse_inputs=[_house_parse_input()]
            )

        staged_bundle = mocks["stage"].call_args[0][1]
        assert isinstance(staged_bundle, DisclosuresBundle)

    def test_stage_receives_bundle_with_correct_entry_count(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext() as mocks:
            run_disclosures_bundle_process(
                conn, bundle, local_root=local_root, parse_inputs=[_house_parse_input()]
            )

        staged_bundle = mocks["stage"].call_args[0][1]
        assert len(staged_bundle.artifacts) == 1
        assert staged_bundle.artifacts[0].source_record_id == _HOUSE_SOURCE_RECORD_ID

    def test_stage_receives_bundle_with_correct_sha256(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext() as mocks:
            run_disclosures_bundle_process(
                conn, bundle, local_root=local_root, parse_inputs=[_house_parse_input()]
            )

        staged_bundle = mocks["stage"].call_args[0][1]
        assert staged_bundle.artifacts[0].sha256 == _SHA256

    def test_stage_receives_local_root(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext() as mocks:
            run_disclosures_bundle_process(
                conn, bundle, local_root=local_root, parse_inputs=[_house_parse_input()]
            )

        _, stage_kwargs = mocks["stage"].call_args
        assert stage_kwargs["local_root"] == local_root


# ---------------------------------------------------------------------------
# Explicit parse inputs reach run_disclosure_parse_runtime
# ---------------------------------------------------------------------------


class TestBundleProcessParseInputsE2E:
    """Prove parse_inputs are forwarded and the bundle index provider is wired."""

    def test_parse_session_called_once_per_input(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext() as mocks:
            run_disclosures_bundle_process(
                conn, bundle, local_root=local_root, parse_inputs=[_house_parse_input()]
            )

        mocks["parse_session"].assert_called_once()

    def test_parse_session_receives_correct_source_artifact_id(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        artifact_row = _house_artifact_row(artifact_id=42)
        conn = MagicMock()

        with _E2EPatchContext(stage_result=_staged_result(artifact_row)) as mocks:
            run_disclosures_bundle_process(
                conn,
                bundle,
                local_root=local_root,
                parse_inputs=[_house_parse_input(artifact_row=artifact_row)],
            )

        _, parse_kwargs = mocks["parse_session"].call_args
        assert parse_kwargs["source_artifact_id"] == 42

    def test_no_parse_session_when_parse_inputs_is_empty(self, tmp_path):
        local_root = tmp_path / "artifacts"
        # Write the artifact so sha256 verification passes (it runs over
        # bundle.artifacts regardless of parse_inputs length).
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        # Empty parse_inputs skips all parse sessions.
        with _E2EPatchContext() as mocks:
            result = run_disclosures_bundle_process(
                conn, bundle, local_root=local_root, parse_inputs=[]
            )

        mocks["parse_session"].assert_not_called()
        assert result.parse_result.succeeded_count == 0

    def test_bundle_index_provider_resolves_match_for_input_row(self, tmp_path):
        """The _BundleIndexProvider built inside the pipeline resolves the
        house entry's index_row for the supplied artifact row.

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
                run_disclosures_bundle_process(
                    conn,
                    bundle,
                    local_root=local_root,
                    parse_inputs=[_house_parse_input(artifact_row=artifact_row)],
                )

        assert len(captured_matches) == 1
        assert captured_matches[0].index_row is not None
        assert isinstance(captured_matches[0].index_row, HouseBundledIndexRow)
        assert captured_matches[0].index_row.last_name == "Smith"


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

        with _E2EPatchContext(session=_parse_session()):
            result = run_disclosures_bundle_process(
                conn,
                bundle,
                local_root=local_root,
                parse_inputs=[_house_parse_input()],
            )

        assert result.transform_count == 0

    def test_parse_result_succeeded_count_matches_session_count(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext(session=_parse_session(run_id=5)):
            result = run_disclosures_bundle_process(
                conn,
                bundle,
                local_root=local_root,
                parse_inputs=[_house_parse_input()],
            )

        assert result.parse_result.succeeded_count == 1
        assert result.parse_result.failed_count == 0

    def test_load_receives_empty_transformed_list_when_all_skipped(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()

        with _E2EPatchContext() as mocks:
            run_disclosures_bundle_process(
                conn,
                bundle,
                local_root=local_root,
                parse_inputs=[_house_parse_input()],
            )

        _, load_args = mocks["load"].call_args
        # positional second arg is the transformed list
        transformed_list = mocks["load"].call_args[0][1]
        assert transformed_list == []

    def test_result_is_typed_disclosures_bundle_process_result(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        conn = MagicMock()
        lr = _load_result()

        with _E2EPatchContext(load_result=lr):
            result = run_disclosures_bundle_process(
                conn,
                bundle,
                local_root=local_root,
                parse_inputs=[_house_parse_input()],
            )

        assert isinstance(result, DisclosuresBundleProcessResult)
        assert result.load_result is lr

    def test_stage_result_propagated_to_pipeline_result(self, tmp_path):
        local_root = tmp_path / "artifacts"
        _write_artifact(local_root, _HOUSE_STORAGE_URI, _STUB_BYTES)
        bundle = disclosures_bundle_from_dict(_house_bundle_dict())
        sr = _staged_result()
        conn = MagicMock()

        with _E2EPatchContext(stage_result=sr):
            result = run_disclosures_bundle_process(
                conn,
                bundle,
                local_root=local_root,
                parse_inputs=[_house_parse_input()],
            )

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
            result = run_disclosures_bundle_process(
                conn,
                bundle,
                local_root=local_root,
                parse_inputs=[_senate_parse_input(artifact_row=senate_row)],
            )

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
