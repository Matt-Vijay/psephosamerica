"""Tests for src/runtime/disclosures_bundle_process.py.

No DB, no network, no filesystem access.  All four pipeline boundaries
(stage, parse, transform, load) are patched so the tests exercise only the
orchestration logic in run_disclosures_bundle_process.  SHA-256 verification
is patched where tested so no real files are required.
"""
from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from src.runtime.disclosures_bundle_process import (
    DisclosuresBundleProcessResult,
    _BundleIndexProvider,
    _validate_bundle,
    run_disclosures_bundle_process,
)
from src.runtime.disclosures_parse import IndexMatchProvider

# ---------------------------------------------------------------------------
# Patch target strings
# ---------------------------------------------------------------------------

_STAGE = "src.runtime.disclosures_bundle_process.stage_disclosures_bundle"
_PARSE = "src.runtime.disclosures_bundle_process.run_disclosure_parse_runtime"
_TRANSFORM = "src.runtime.disclosures_bundle_process.transform_parse_sessions"
_LOAD = "src.runtime.disclosures_bundle_process.run_disclosures_load_runtime"
_VERIFY = "src.runtime.disclosures_bundle_process.verify_entry_sha256"

# ---------------------------------------------------------------------------
# Shared factory helpers
# ---------------------------------------------------------------------------

_LOCAL_ROOT = Path("/tmp/test_bundle_artifacts")


def _bundle(artifacts=()) -> MagicMock:
    """Return a minimal bundle mock with an iterable artifacts attribute."""
    b = MagicMock(name="DisclosureBundle")
    b.artifacts = artifacts
    return b


def _bundle_entry(
    chamber: str = "house",
    filing_year: int = 2024,
    source_record_id: str = "12345",
    sha256: str = "a" * 64,
    storage_uri: str = "house/2024/12345.pdf",
    index_row: object = None,
) -> MagicMock:
    """Return a minimal bundle entry mock."""
    e = MagicMock()
    e.chamber = chamber
    e.filing_year = filing_year
    e.source_record_id = source_record_id
    e.sha256 = sha256
    e.storage_uri = storage_uri
    e.index_row = index_row or MagicMock()
    return e


def _stage_result() -> MagicMock:
    r = MagicMock()
    r.staged_count = 2
    return r


def _parse_result(succeeded: int = 1, failed: int = 0) -> MagicMock:
    r = MagicMock()
    r.succeeded_count = succeeded
    r.failed_count = failed
    r.processed_count = succeeded + failed
    r.parse_sessions = tuple(MagicMock() for _ in range(succeeded))
    return r


def _transform_batch(n: int = 1) -> MagicMock:
    """Return a mock BatchTransformResult with n transformed entries."""
    r = MagicMock()
    r.transformed = [MagicMock() for _ in range(n)]
    r.skipped = []
    return r


def _load_result() -> MagicMock:
    r = MagicMock()
    r.run_id = 99
    return r


@contextlib.contextmanager
def _patch_all(
    stage_result=None,
    parse_result=None,
    transform_batch=None,
    load_result=None,
):
    """Patch all four pipeline boundaries plus SHA-256 verification."""
    sr = stage_result if stage_result is not None else _stage_result()
    pr = parse_result if parse_result is not None else _parse_result()
    tb = transform_batch if transform_batch is not None else _transform_batch()
    lr = load_result if load_result is not None else _load_result()

    with (
        patch(_STAGE, return_value=sr) as mock_stage,
        patch(_PARSE, return_value=pr) as mock_parse,
        patch(_TRANSFORM, return_value=tb) as mock_transform,
        patch(_LOAD, return_value=lr) as mock_load,
        patch(_VERIFY) as mock_verify,
    ):
        yield {
            "stage": mock_stage,
            "parse": mock_parse,
            "transform": mock_transform,
            "load": mock_load,
            "verify": mock_verify,
        }


# ---------------------------------------------------------------------------
# Bundle validation
# ---------------------------------------------------------------------------


class TestBundleValidation:
    def test_valid_bundle_passes_through(self):
        b = _bundle()
        _validate_bundle(b)  # must not raise

    def test_bundle_without_artifacts_raises_type_error(self):
        import pytest
        bad = object()
        with pytest.raises(TypeError, match="artifacts"):
            _validate_bundle(bad)

    def test_bundle_validation_called_before_stage(self):
        """Validation must precede any DB writes."""
        call_order: list[str] = []

        class _Validator:
            def __init__(self):
                self._orig = _validate_bundle

            def __enter__(self):
                import src.runtime.disclosures_bundle_process as mod
                self._orig_fn = mod._validate_bundle

                def _patched(b):
                    call_order.append("validate")
                    return self._orig_fn(b)

                mod._validate_bundle = _patched
                return self

            def __exit__(self, *_):
                import src.runtime.disclosures_bundle_process as mod
                mod._validate_bundle = self._orig_fn

        sr, pr, tb, lr = (
            _stage_result(), _parse_result(), _transform_batch(), _load_result()
        )
        with (
            _Validator(),
            patch(_STAGE, side_effect=lambda *a, **k: call_order.append("stage") or sr),
            patch(_PARSE, return_value=pr),
            patch(_TRANSFORM, return_value=tb),
            patch(_LOAD, return_value=lr),
            patch(_VERIFY),
        ):
            run_disclosures_bundle_process(MagicMock(), _bundle(), local_root=_LOCAL_ROOT)

        assert call_order.index("validate") < call_order.index("stage")

    def test_invalid_bundle_raises_before_staging(self):
        import pytest
        with patch(_STAGE) as mock_stage:
            with pytest.raises(TypeError):
                run_disclosures_bundle_process(MagicMock(), object())
        mock_stage.assert_not_called()


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_returns_typed_result(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert isinstance(result, DisclosuresBundleProcessResult)

    def test_result_exposes_stage_result(self):
        conn = MagicMock()
        sr = _stage_result()
        with _patch_all(stage_result=sr):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert result.stage_result is sr

    def test_result_exposes_parse_result(self):
        conn = MagicMock()
        pr = _parse_result()
        with _patch_all(parse_result=pr):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert result.parse_result is pr

    def test_result_exposes_transform_count(self):
        conn = MagicMock()
        tb = _transform_batch(3)
        with _patch_all(transform_batch=tb):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert result.transform_count == 3

    def test_result_exposes_load_result(self):
        conn = MagicMock()
        lr = _load_result()
        with _patch_all(load_result=lr):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert result.load_result is lr

    def test_transform_count_zero_when_nothing_transforms(self):
        conn = MagicMock()
        tb = _transform_batch(0)
        with _patch_all(transform_batch=tb):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert result.transform_count == 0


# ---------------------------------------------------------------------------
# Step ordering — stage → parse → transform → load
# ---------------------------------------------------------------------------


class TestStepOrdering:
    @staticmethod
    def _run_with_call_order() -> list[str]:
        call_order: list[str] = []
        sr, pr, tb, lr = (
            _stage_result(),
            _parse_result(),
            _transform_batch(),
            _load_result(),
        )
        with (
            patch(_STAGE, side_effect=lambda *a, **k: call_order.append("stage") or sr),
            patch(_PARSE, side_effect=lambda *a, **k: call_order.append("parse") or pr),
            patch(_TRANSFORM, side_effect=lambda *a, **k: call_order.append("transform") or tb),
            patch(_LOAD, side_effect=lambda *a, **k: call_order.append("load") or lr),
            patch(_VERIFY),
        ):
            run_disclosures_bundle_process(MagicMock(), _bundle(), local_root=_LOCAL_ROOT)
        return call_order

    def test_stage_called_before_parse(self):
        order = self._run_with_call_order()
        assert order.index("stage") < order.index("parse")

    def test_parse_called_before_transform(self):
        order = self._run_with_call_order()
        assert order.index("parse") < order.index("transform")

    def test_transform_called_before_load(self):
        order = self._run_with_call_order()
        assert order.index("transform") < order.index("load")

    def test_all_four_called_exactly_once(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        mocks["stage"].assert_called_once()
        mocks["parse"].assert_called_once()
        mocks["transform"].assert_called_once()
        mocks["load"].assert_called_once()


# ---------------------------------------------------------------------------
# Stage wiring — conn, bundle, local_root forwarded to stage_disclosures_bundle
# ---------------------------------------------------------------------------


class TestStageWiring:
    def test_conn_forwarded_to_stage(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert mocks["stage"].call_args[0][0] is conn

    def test_bundle_forwarded_to_stage(self):
        conn = MagicMock()
        b = _bundle()
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, b, local_root=_LOCAL_ROOT)
        assert mocks["stage"].call_args[0][1] is b

    def test_local_root_forwarded_to_stage(self):
        conn = MagicMock()
        root = Path("/data/my_disclosures")
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=root)
        _, kwargs = mocks["stage"].call_args
        assert kwargs["local_root"] == root

    def test_local_root_none_forwarded_to_stage(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=None)
        _, kwargs = mocks["stage"].call_args
        assert kwargs.get("local_root") is None


# ---------------------------------------------------------------------------
# Parse wiring — conn, local_root, index_provider forwarded
# ---------------------------------------------------------------------------


class TestParseWiring:
    def test_conn_forwarded_to_parse(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert mocks["parse"].call_args[0][0] is conn

    def test_local_root_forwarded_to_parse(self):
        conn = MagicMock()
        root = Path("/data/disclosures")
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=root)
        _, kwargs = mocks["parse"].call_args
        assert kwargs["local_root"] == root

    def test_local_root_none_forwarded_to_parse(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=None)
        _, kwargs = mocks["parse"].call_args
        assert kwargs.get("local_root") is None

    def test_index_provider_passed_to_parse(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        _, kwargs = mocks["parse"].call_args
        assert "index_provider" in kwargs

    def test_index_provider_satisfies_protocol(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        _, kwargs = mocks["parse"].call_args
        assert isinstance(kwargs["index_provider"], IndexMatchProvider)

    def test_index_provider_is_bundle_backed(self):
        """Provider must be a _BundleIndexProvider, not the live-fetch path."""
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        _, kwargs = mocks["parse"].call_args
        assert isinstance(kwargs["index_provider"], _BundleIndexProvider)


# ---------------------------------------------------------------------------
# Transform wiring — conn and parse_sessions forwarded
# ---------------------------------------------------------------------------


class TestTransformWiring:
    def test_conn_forwarded_to_transform(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert mocks["transform"].call_args[0][0] is conn

    def test_parse_sessions_forwarded_to_transform(self):
        conn = MagicMock()
        pr = _parse_result(succeeded=3)
        with _patch_all(parse_result=pr) as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert mocks["transform"].call_args[0][1] is pr.parse_sessions

    def test_empty_parse_sessions_forwarded_when_all_failed(self):
        conn = MagicMock()
        pr = _parse_result(succeeded=0, failed=2)
        with _patch_all(parse_result=pr) as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert mocks["transform"].call_args[0][1] == ()


# ---------------------------------------------------------------------------
# Load wiring — conn and transform.transformed forwarded
# ---------------------------------------------------------------------------


class TestLoadWiring:
    def test_conn_forwarded_to_load(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert mocks["load"].call_args[0][0] is conn

    def test_transformed_list_forwarded_to_load(self):
        conn = MagicMock()
        tb = _transform_batch(2)
        with _patch_all(transform_batch=tb) as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        args, _ = mocks["load"].call_args
        assert args[1] is tb.transformed

    def test_empty_transformed_forwarded_when_nothing_transforms(self):
        conn = MagicMock()
        tb = _transform_batch(0)
        with _patch_all(transform_batch=tb) as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        args, _ = mocks["load"].call_args
        assert args[1] == []


# ---------------------------------------------------------------------------
# transform_count reflects actual length of BatchTransformResult.transformed
# ---------------------------------------------------------------------------


class TestTransformCount:
    def test_transform_count_matches_batch_transformed_length(self):
        conn = MagicMock()
        for n in (0, 1, 3, 7):
            tb = _transform_batch(n)
            with _patch_all(transform_batch=tb):
                result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
            assert result.transform_count == n, f"expected {n}, got {result.transform_count}"

    def test_transform_count_independent_of_parse_succeeded_count(self):
        """transform_count reflects batch.transformed length, not parse succeeded count."""
        conn = MagicMock()
        pr = _parse_result(succeeded=5)
        tb = _transform_batch(2)
        with _patch_all(parse_result=pr, transform_batch=tb):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert result.transform_count == 2


# ---------------------------------------------------------------------------
# SHA-256 verification — resolve bundle files step
# ---------------------------------------------------------------------------


class TestSha256Verification:
    def test_verify_called_once_per_entry_when_local_root_set(self):
        conn = MagicMock()
        entries = (_bundle_entry(source_record_id="1"), _bundle_entry(source_record_id="2"))
        b = _bundle(artifacts=entries)
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, b, local_root=_LOCAL_ROOT)
        assert mocks["verify"].call_count == 2

    def test_verify_receives_entry_and_local_root(self):
        conn = MagicMock()
        entry = _bundle_entry()
        b = _bundle(artifacts=(entry,))
        root = Path("/storage/root")
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, b, local_root=root)
        mocks["verify"].assert_called_once_with(entry, root)

    def test_verify_not_called_when_local_root_is_none(self):
        conn = MagicMock()
        entries = (_bundle_entry(),)
        b = _bundle(artifacts=entries)
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, b, local_root=None)
        mocks["verify"].assert_not_called()

    def test_verify_called_before_parse(self):
        """SHA-256 checks must complete before parse begins."""
        call_order: list[str] = []
        entry = _bundle_entry()
        b = _bundle(artifacts=(entry,))
        sr, pr, tb, lr = (
            _stage_result(), _parse_result(), _transform_batch(), _load_result(),
        )
        with (
            patch(_STAGE, return_value=sr),
            patch(_VERIFY, side_effect=lambda *a: call_order.append("verify")),
            patch(_PARSE, side_effect=lambda *a, **k: call_order.append("parse") or pr),
            patch(_TRANSFORM, return_value=tb),
            patch(_LOAD, return_value=lr),
        ):
            run_disclosures_bundle_process(MagicMock(), b, local_root=_LOCAL_ROOT)
        assert call_order.index("verify") < call_order.index("parse")

    def test_verify_called_for_every_entry_in_order(self):
        conn = MagicMock()
        e1 = _bundle_entry(source_record_id="A")
        e2 = _bundle_entry(source_record_id="B")
        e3 = _bundle_entry(source_record_id="C")
        b = _bundle(artifacts=(e1, e2, e3))
        root = _LOCAL_ROOT
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, b, local_root=root)
        assert mocks["verify"].call_args_list == [
            call(e1, root),
            call(e2, root),
            call(e3, root),
        ]


# ---------------------------------------------------------------------------
# _BundleIndexProvider unit tests
# ---------------------------------------------------------------------------


class TestBundleIndexProvider:
    def _make_provider(self, entries=()) -> _BundleIndexProvider:
        bundle = _bundle(artifacts=entries)
        return _BundleIndexProvider(bundle)

    def test_empty_bundle_returns_no_match_for_any_artifact(self):
        provider = self._make_provider()
        row = {"chamber": "house", "filing_year": 2024, "source_record_id": "99", "id": 1}
        matches = provider.load_matches([row])
        assert len(matches) == 1
        assert matches[0].index_row is None
        assert matches[0].artifact is row

    def test_resolves_matched_entry_index_row(self):
        index_row = MagicMock(name="IndexRow")
        entry = _bundle_entry(
            chamber="house",
            filing_year=2024,
            source_record_id="12345",
            index_row=index_row,
        )
        provider = self._make_provider(entries=(entry,))
        row = {"chamber": "house", "filing_year": 2024, "source_record_id": "12345", "id": 7}
        matches = provider.load_matches([row])
        assert matches[0].index_row is index_row

    def test_no_match_when_source_record_id_differs(self):
        entry = _bundle_entry(source_record_id="AAAAAA")
        provider = self._make_provider(entries=(entry,))
        row = {"chamber": "house", "filing_year": 2024, "source_record_id": "BBBBBB", "id": 8}
        matches = provider.load_matches([row])
        assert matches[0].index_row is None

    def test_no_match_when_chamber_is_none(self):
        entry = _bundle_entry(chamber="house", source_record_id="X")
        provider = self._make_provider(entries=(entry,))
        row = {"chamber": None, "filing_year": 2024, "source_record_id": "X", "id": 9}
        matches = provider.load_matches([row])
        assert matches[0].index_row is None

    def test_no_match_when_filing_year_is_none(self):
        entry = _bundle_entry(filing_year=2024, source_record_id="X")
        provider = self._make_provider(entries=(entry,))
        row = {"chamber": "house", "filing_year": None, "source_record_id": "X", "id": 10}
        matches = provider.load_matches([row])
        assert matches[0].index_row is None

    def test_matches_senate_entry(self):
        index_row = MagicMock(name="SenateRow")
        entry = _bundle_entry(
            chamber="senate",
            filing_year=2023,
            source_record_id="uuid-xyz",
            index_row=index_row,
        )
        provider = self._make_provider(entries=(entry,))
        row = {"chamber": "senate", "filing_year": 2023, "source_record_id": "uuid-xyz", "id": 11}
        matches = provider.load_matches([row])
        assert matches[0].index_row is index_row

    def test_returns_same_count_as_input(self):
        provider = self._make_provider()
        rows = [
            {"chamber": "house", "filing_year": 2024, "source_record_id": str(i), "id": i}
            for i in range(5)
        ]
        matches = provider.load_matches(rows)
        assert len(matches) == 5

    def test_preserves_artifact_reference_in_matches(self):
        provider = self._make_provider()
        row = {"chamber": "house", "filing_year": 2024, "source_record_id": "Z", "id": 99}
        matches = provider.load_matches([row])
        assert matches[0].artifact is row

    def test_load_member_rows_delegates_to_db(self):
        """load_member_rows must call fetch_member_rows_for_disclosures, not the live index."""
        provider = self._make_provider()
        conn = MagicMock()
        db_rows = [{"bioguide_id": "S000001"}]
        with patch(
            "src.runtime.disclosures_bundle_process.fetch_member_rows_for_disclosures",
            return_value=db_rows,
        ) as mock_fetch:
            result = provider.load_member_rows(conn, chamber="senate")
        mock_fetch.assert_called_once_with(conn, chamber="senate")
        assert result is db_rows

    def test_satisfies_index_match_provider_protocol(self):
        provider = self._make_provider()
        assert isinstance(provider, IndexMatchProvider)
