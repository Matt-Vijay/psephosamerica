"""Tests for src/runtime/disclosures_bundle_process.py.

No DB, no network, no filesystem access.  All pipeline boundaries
(stage, parse inputs auto-build, parse, transform, load) are patched so the
tests exercise only the orchestration logic in run_disclosures_bundle_process.
SHA-256 verification (for the explicit-parse-inputs path) is patched where
tested so no real files are required.

Flow under test: validate → stage → parse inputs → parse → transform → load
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from src.runtime.disclosures_bundle import disclosures_bundle_from_dict
from src.runtime.disclosures_bundle_process import (
    DisclosuresBundleProcessResult,
    _BundleIndexProvider,
    _build_parse_inputs,
    _validate_bundle,
    run_disclosures_bundle_process,
)
from src.runtime.disclosures_parse import IndexMatchProvider
from src.runtime.disclosures_transform import BatchTransformResult, SkippedSession

# ---------------------------------------------------------------------------
# Patch target strings
# ---------------------------------------------------------------------------

_STAGE = "src.runtime.disclosures_bundle_process.stage_disclosures_bundle"
_BUILD_PARSE_INPUTS = "src.runtime.disclosures_bundle_process._build_parse_inputs"
_PARSE = "src.runtime.disclosures_bundle_process.run_disclosure_parse_runtime"
_TRANSFORM = "src.runtime.disclosures_bundle_process.transform_parse_sessions"
_LOAD = "src.runtime.disclosures_bundle_process.run_disclosures_load_runtime"
_VERIFY = "src.runtime.disclosures_bundle_process.verify_entry_sha256"

# ---------------------------------------------------------------------------
# Shared factory helpers
# ---------------------------------------------------------------------------

_LOCAL_ROOT = Path("/tmp/test_bundle_artifacts")


def _real_bundle_with_doc_id_mismatch():
    return disclosures_bundle_from_dict(
        {
            "artifacts": [
                {
                    "source_record_id": "DOC1",
                    "chamber": "house",
                    "filing_year": 2024,
                    "storage_uri": "house/2024/DOC1.pdf",
                    "source_url": "https://disclosures.house.gov/public_disc/ptr-pdfs/2024/DOC1.pdf",
                    "source_slug": "house-disclosures",
                    "artifact_kind": "pdf",
                    "sha256": "a" * 64,
                    "index_row": {
                        "last_name": "Smith",
                        "first_name": "Jane",
                        "suffix": "",
                        "raw_filing_type": "P",
                        "state_dst": "CA08",
                        "filing_date": "2024-01-15",
                        "doc_id": "OTHER-DOC",
                        "filing_kind": "ptr",
                    },
                }
            ]
        }
    )


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
    r.artifact_rows = ()
    return r


def _parse_result(succeeded: int = 1, failed: int = 0) -> MagicMock:
    r = MagicMock()
    r.succeeded_count = succeeded
    r.failed_count = failed
    r.processed_count = succeeded + failed
    r.parse_sessions = tuple(MagicMock() for _ in range(succeeded))
    return r


def _transform_batch(n: int = 1, n_skipped: int = 0) -> BatchTransformResult:
    """Return a BatchTransformResult with the given counts."""
    return BatchTransformResult(
        transformed=[MagicMock() for _ in range(n)],
        skipped=[
            SkippedSession(run_id=None, reason_code="no_parse_result") for _ in range(n_skipped)
        ],
    )


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
    built_parse_inputs=None,
):
    """Patch all pipeline boundaries plus SHA-256 verification.

    Patches:
      stage               — stage_disclosures_bundle
      build_parse_inputs  — _build_parse_inputs (step 3 auto-build path)
      parse               — run_disclosure_parse_runtime
      transform           — transform_parse_sessions
      load                — run_disclosures_load_runtime
      verify              — verify_entry_sha256 (explicit-inputs SHA-256 path)
    """
    sr = stage_result if stage_result is not None else _stage_result()
    pr = parse_result if parse_result is not None else _parse_result()
    tb = transform_batch if transform_batch is not None else _transform_batch()
    lr = load_result if load_result is not None else _load_result()
    bpi = built_parse_inputs if built_parse_inputs is not None else []

    with (
        patch(_STAGE, return_value=sr) as mock_stage,
        patch(_BUILD_PARSE_INPUTS, return_value=bpi) as mock_build,
        patch(_PARSE, return_value=pr) as mock_parse,
        patch(_TRANSFORM, return_value=tb) as mock_transform,
        patch(_LOAD, return_value=lr) as mock_load,
        patch(_VERIFY) as mock_verify,
    ):
        yield {
            "stage": mock_stage,
            "build_parse_inputs": mock_build,
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

        sr, pr, tb, lr = (_stage_result(), _parse_result(), _transform_batch(), _load_result())
        with (
            _Validator(),
            patch(_STAGE, side_effect=lambda *a, **k: call_order.append("stage") or sr),
            patch(_BUILD_PARSE_INPUTS, return_value=[]),
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

    def test_semantically_invalid_bundle_raises_before_staging(self):
        import pytest

        with patch(_STAGE) as mock_stage:
            with pytest.raises(ValueError, match="doc_id"):
                run_disclosures_bundle_process(MagicMock(), _real_bundle_with_doc_id_mismatch())
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

    def test_result_exposes_skipped_transform_count(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert hasattr(result, "skipped_transform_count")

    def test_result_exposes_skipped_sessions(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert hasattr(result, "skipped_sessions")


# ---------------------------------------------------------------------------
# Step ordering — stage → parse inputs → parse → transform → load
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
            patch(
                _BUILD_PARSE_INPUTS,
                side_effect=lambda *a, **k: call_order.append("build_parse_inputs") or [],
            ),
            patch(_PARSE, side_effect=lambda *a, **k: call_order.append("parse") or pr),
            patch(_TRANSFORM, side_effect=lambda *a, **k: call_order.append("transform") or tb),
            patch(_LOAD, side_effect=lambda *a, **k: call_order.append("load") or lr),
            patch(_VERIFY),
        ):
            run_disclosures_bundle_process(MagicMock(), _bundle(), local_root=_LOCAL_ROOT)
        return call_order

    def test_stage_called_before_parse_inputs(self):
        order = self._run_with_call_order()
        assert order.index("stage") < order.index("build_parse_inputs")

    def test_parse_inputs_called_before_parse(self):
        order = self._run_with_call_order()
        assert order.index("build_parse_inputs") < order.index("parse")

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
# Parse inputs step — auto-build path (parse_inputs=None)
# ---------------------------------------------------------------------------


class TestParseInputsAutoBuild:
    """Step 3: when parse_inputs=None, _build_parse_inputs is used to build
    explicit parse inputs before parse runs."""

    def test_build_parse_inputs_called_when_parse_inputs_none_and_local_root_set(self):
        conn = MagicMock()
        b = _bundle()
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, b, local_root=_LOCAL_ROOT)
        mocks["build_parse_inputs"].assert_called_once()

    def test_build_parse_inputs_receives_bundle(self):
        conn = MagicMock()
        b = _bundle()
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, b, local_root=_LOCAL_ROOT)
        args, _ = mocks["build_parse_inputs"].call_args
        assert args[0] is b

    def test_build_parse_inputs_receives_stage_result(self):
        conn = MagicMock()
        sr = _stage_result()
        with _patch_all(stage_result=sr) as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        args, _ = mocks["build_parse_inputs"].call_args
        assert args[1] is sr

    def test_build_parse_inputs_receives_local_root(self):
        conn = MagicMock()
        root = Path("/storage/local")
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=root)
        args, _ = mocks["build_parse_inputs"].call_args
        assert args[2] == root

    def test_build_parse_inputs_receives_none_local_root(self):
        conn = MagicMock()
        b = _bundle()
        sr = _stage_result()
        with _patch_all(stage_result=sr) as mocks:
            run_disclosures_bundle_process(conn, b, local_root=None)
        mocks["build_parse_inputs"].assert_called_once_with(b, sr, None)

    def test_build_parse_inputs_not_called_when_explicit_inputs_provided(self):
        conn = MagicMock()
        explicit_inputs = [MagicMock()]
        with _patch_all() as mocks:
            run_disclosures_bundle_process(
                conn, _bundle(), local_root=_LOCAL_ROOT, parse_inputs=explicit_inputs
            )
        mocks["build_parse_inputs"].assert_not_called()

    def test_built_inputs_forwarded_to_parse(self):
        """The list returned by _build_parse_inputs is passed to the parse step."""
        conn = MagicMock()
        built = [MagicMock(), MagicMock()]
        with _patch_all(built_parse_inputs=built) as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        _, parse_kwargs = mocks["parse"].call_args
        assert parse_kwargs["parse_inputs"] is built

    def test_built_inputs_forwarded_to_parse_when_local_root_is_none(self):
        """Auto-built explicit parse inputs are forwarded even without local_root."""
        conn = MagicMock()
        built = [MagicMock()]
        with _patch_all(built_parse_inputs=built) as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=None)
        _, parse_kwargs = mocks["parse"].call_args
        assert parse_kwargs["parse_inputs"] is built


# ---------------------------------------------------------------------------
# Parse inputs step — explicit inputs path (SHA-256 verify per bundle entry)
# ---------------------------------------------------------------------------


class TestParseInputsExplicit:
    """When explicit parse_inputs are provided with local_root set,
    verify_entry_sha256 must be called for each bundle entry."""

    def test_verify_called_once_per_entry_when_explicit_inputs_and_local_root(self):
        conn = MagicMock()
        entries = (_bundle_entry(source_record_id="1"), _bundle_entry(source_record_id="2"))
        b = _bundle(artifacts=entries)
        explicit_inputs = [MagicMock()]
        with _patch_all() as mocks:
            run_disclosures_bundle_process(
                conn, b, local_root=_LOCAL_ROOT, parse_inputs=explicit_inputs
            )
        assert mocks["verify"].call_count == 2

    def test_verify_receives_entry_and_local_root(self):
        conn = MagicMock()
        entry = _bundle_entry()
        b = _bundle(artifacts=(entry,))
        root = Path("/storage/root")
        explicit_inputs = [MagicMock()]
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, b, local_root=root, parse_inputs=explicit_inputs)
        mocks["verify"].assert_called_once_with(entry, root)

    def test_verify_not_called_when_local_root_is_none_and_explicit_inputs(self):
        conn = MagicMock()
        entries = (_bundle_entry(),)
        b = _bundle(artifacts=entries)
        explicit_inputs = [MagicMock()]
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, b, local_root=None, parse_inputs=explicit_inputs)
        mocks["verify"].assert_not_called()

    def test_verify_not_called_when_parse_inputs_none_and_local_root_set(self):
        """Auto-build path (parse_inputs=None) uses _build_parse_inputs, not verify."""
        conn = MagicMock()
        entries = (_bundle_entry(),)
        b = _bundle(artifacts=entries)
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, b, local_root=_LOCAL_ROOT)
        mocks["verify"].assert_not_called()

    def test_verify_called_before_parse_when_explicit_inputs(self):
        """SHA-256 checks must complete before parse begins."""
        call_order: list[str] = []
        entry = _bundle_entry()
        b = _bundle(artifacts=(entry,))
        sr, pr, tb, lr = (
            _stage_result(),
            _parse_result(),
            _transform_batch(),
            _load_result(),
        )
        explicit_inputs = [MagicMock()]
        with (
            patch(_STAGE, return_value=sr),
            patch(_BUILD_PARSE_INPUTS, return_value=[]),
            patch(_VERIFY, side_effect=lambda *a: call_order.append("verify")),
            patch(_PARSE, side_effect=lambda *a, **k: call_order.append("parse") or pr),
            patch(_TRANSFORM, return_value=tb),
            patch(_LOAD, return_value=lr),
        ):
            run_disclosures_bundle_process(
                MagicMock(), b, local_root=_LOCAL_ROOT, parse_inputs=explicit_inputs
            )
        assert call_order.index("verify") < call_order.index("parse")

    def test_verify_called_for_every_entry_in_order_when_explicit_inputs(self):
        conn = MagicMock()
        e1 = _bundle_entry(source_record_id="A")
        e2 = _bundle_entry(source_record_id="B")
        e3 = _bundle_entry(source_record_id="C")
        b = _bundle(artifacts=(e1, e2, e3))
        root = _LOCAL_ROOT
        explicit_inputs = [MagicMock()]
        with _patch_all() as mocks:
            run_disclosures_bundle_process(conn, b, local_root=root, parse_inputs=explicit_inputs)
        assert mocks["verify"].call_args_list == [
            call(e1, root),
            call(e2, root),
            call(e3, root),
        ]

    def test_explicit_inputs_forwarded_to_parse(self):
        conn = MagicMock()
        explicit_inputs = [MagicMock(), MagicMock()]
        with _patch_all() as mocks:
            run_disclosures_bundle_process(
                conn, _bundle(), local_root=_LOCAL_ROOT, parse_inputs=explicit_inputs
            )
        _, parse_kwargs = mocks["parse"].call_args
        assert parse_kwargs["parse_inputs"] is explicit_inputs


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
# skipped_transform_count — from batch.skipped, not arithmetic on succeeded
# ---------------------------------------------------------------------------


class TestSkippedTransformCount:
    def test_skipped_count_zero_when_nothing_skipped(self):
        conn = MagicMock()
        with _patch_all(transform_batch=_transform_batch(n=3, n_skipped=0)):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert result.skipped_transform_count == 0

    def test_skipped_count_equals_batch_skipped_length(self):
        conn = MagicMock()
        tb = _transform_batch(n=2, n_skipped=3)
        with _patch_all(transform_batch=tb):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert result.skipped_transform_count == 3

    def test_skipped_count_all_skipped(self):
        conn = MagicMock()
        tb = _transform_batch(n=0, n_skipped=4)
        with _patch_all(transform_batch=tb):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert result.skipped_transform_count == 4

    def test_transform_plus_skipped_from_batch(self):
        """transform_count + skipped_transform_count == len(batch.transformed) + len(batch.skipped)."""
        conn = MagicMock()
        for n_transformed, n_skipped in ((0, 0), (1, 0), (0, 1), (4, 3), (7, 7)):
            tb = _transform_batch(n=n_transformed, n_skipped=n_skipped)
            with _patch_all(transform_batch=tb):
                result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
            total = result.transform_count + result.skipped_transform_count
            assert total == n_transformed + n_skipped


# ---------------------------------------------------------------------------
# skipped_sessions — explicit SkippedSession records exposed on result
# ---------------------------------------------------------------------------


class TestSkippedSessions:
    def test_skipped_sessions_empty_when_nothing_skipped(self):
        conn = MagicMock()
        with _patch_all(transform_batch=_transform_batch(n=2, n_skipped=0)):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert result.skipped_sessions == ()

    def test_skipped_sessions_is_tuple(self):
        conn = MagicMock()
        with _patch_all(transform_batch=_transform_batch(n=1, n_skipped=2)):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert isinstance(result.skipped_sessions, tuple)

    def test_skipped_sessions_length_equals_skipped_count(self):
        conn = MagicMock()
        tb = _transform_batch(n=1, n_skipped=3)
        with _patch_all(transform_batch=tb):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert len(result.skipped_sessions) == 3

    def test_skipped_sessions_contains_skipped_session_instances(self):
        conn = MagicMock()
        tb = _transform_batch(n=0, n_skipped=2)
        with _patch_all(transform_batch=tb):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        for s in result.skipped_sessions:
            assert isinstance(s, SkippedSession)

    def test_skipped_sessions_reason_codes_preserved(self):
        tb = BatchTransformResult(
            transformed=[],
            skipped=[
                SkippedSession(run_id=1, reason_code="no_parse_result"),
                SkippedSession(run_id=2, reason_code="no_parsed_document"),
                SkippedSession(run_id=3, reason_code="unresolved_member_identity"),
            ],
        )
        conn = MagicMock()
        with _patch_all(transform_batch=tb):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        codes = [s.reason_code for s in result.skipped_sessions]
        assert codes == [
            "no_parse_result",
            "no_parsed_document",
            "unresolved_member_identity",
        ]

    def test_skipped_sessions_run_ids_preserved(self):
        tb = BatchTransformResult(
            transformed=[],
            skipped=[
                SkippedSession(run_id=10, reason_code="no_parse_result"),
                SkippedSession(run_id=20, reason_code="no_parsed_document"),
            ],
        )
        conn = MagicMock()
        with _patch_all(transform_batch=tb):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        run_ids = [s.run_id for s in result.skipped_sessions]
        assert run_ids == [10, 20]

    def test_skipped_count_equals_skipped_sessions_length(self):
        tb = _transform_batch(n=2, n_skipped=4)
        conn = MagicMock()
        with _patch_all(transform_batch=tb):
            result = run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert result.skipped_transform_count == len(result.skipped_sessions)

    def test_load_does_not_receive_skipped_sessions(self):
        """Skipped sessions must NOT be forwarded to the load layer."""
        conn = MagicMock()
        tb = _transform_batch(n=1, n_skipped=3)
        with _patch_all(transform_batch=tb) as mocks:
            run_disclosures_bundle_process(conn, _bundle(), local_root=_LOCAL_ROOT)
        assert len(mocks["load"].call_args[0][1]) == 1


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

    def test_no_match_when_filing_year_is_boolean(self):
        index_row = MagicMock(name="YearOneRow")
        entry = _bundle_entry(filing_year=1, source_record_id="X", index_row=index_row)
        provider = self._make_provider(entries=(entry,))
        row = {"chamber": "house", "filing_year": True, "source_record_id": "X", "id": 10}
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


# ---------------------------------------------------------------------------
# _build_parse_inputs unit tests
# ---------------------------------------------------------------------------


class TestBuildParseInputs:
    """Unit tests for the _build_parse_inputs helper.

    Uses real read_and_verify_entry calls via real on-disk files in tmp_path.
    """

    def test_empty_bundle_produces_empty_inputs(self, tmp_path):
        bundle = _bundle(artifacts=())
        sr = _stage_result()
        sr.artifact_rows = ()
        inputs = _build_parse_inputs(bundle, sr, tmp_path)
        assert inputs == []

    def test_one_artifact_produces_one_input(self, tmp_path):
        import hashlib

        data = b"test-content-abc"
        sha256 = hashlib.sha256(data).hexdigest()
        dest = tmp_path / "house" / "2024" / "99.pdf"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

        entry = _bundle_entry(
            source_record_id="99",
            chamber="house",
            filing_year=2024,
            storage_uri="house/2024/99.pdf",
            sha256=sha256,
        )
        bundle = _bundle(artifacts=(entry,))
        sr = MagicMock()
        sr.artifact_rows = (
            {"id": 1, "chamber": "house", "filing_year": 2024, "source_record_id": "99"},
        )
        inputs = _build_parse_inputs(bundle, sr, tmp_path)

        assert len(inputs) == 1
        assert inputs[0].source_record_id == "99"
        assert inputs[0].chamber == "house"
        assert inputs[0].local_bytes == data

    def test_input_artifact_row_matches_staged_row(self, tmp_path):
        import hashlib

        data = b"content"
        sha256 = hashlib.sha256(data).hexdigest()
        dest = tmp_path / "house" / "2024" / "42.pdf"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

        entry = _bundle_entry(
            source_record_id="42",
            chamber="house",
            filing_year=2024,
            storage_uri="house/2024/42.pdf",
            sha256=sha256,
        )
        bundle = _bundle(artifacts=(entry,))
        artifact_row = {"id": 7, "chamber": "house", "filing_year": 2024, "source_record_id": "42"}
        sr = MagicMock()
        sr.artifact_rows = (artifact_row,)
        inputs = _build_parse_inputs(bundle, sr, tmp_path)

        assert inputs[0].artifact_row is artifact_row

    def test_sha256_mismatch_raises(self, tmp_path):
        from src.runtime.disclosures_bundle_files import Sha256Mismatch
        import pytest

        dest = tmp_path / "house" / "2024" / "X.pdf"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"wrong content")

        entry = _bundle_entry(source_record_id="X", storage_uri="house/2024/X.pdf", sha256="a" * 64)
        bundle = _bundle(artifacts=(entry,))
        sr = MagicMock()
        sr.artifact_rows = (
            {"id": 1, "chamber": "house", "filing_year": 2024, "source_record_id": "X"},
        )

        with pytest.raises(Sha256Mismatch):
            _build_parse_inputs(bundle, sr, tmp_path)

    def test_skips_staged_row_with_no_matching_bundle_entry(self, tmp_path):
        """A staged row whose source_record_id is not in the bundle is skipped."""
        bundle = _bundle(artifacts=())  # no entries
        sr = MagicMock()
        sr.artifact_rows = (
            {"id": 1, "chamber": "house", "filing_year": 2024, "source_record_id": "ORPHAN"},
        )
        inputs = _build_parse_inputs(bundle, sr, tmp_path)
        assert inputs == []
