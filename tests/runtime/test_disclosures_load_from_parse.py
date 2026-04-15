"""Tests for src/runtime/disclosures_load_from_parse.py."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.runtime.disclosures_load_from_parse import (  # noqa: E402
    DisclosuresParseLoadResult,
    run_disclosures_parse_load_runtime,
)

# ---------------------------------------------------------------------------
# Patch target strings
# ---------------------------------------------------------------------------

_PARSE = "src.runtime.disclosures_load_from_parse.run_disclosure_parse_runtime"
_TRANSFORM = "src.runtime.disclosures_load_from_parse.transform_parse_sessions"
_LOAD = "src.runtime.disclosures_load_from_parse.run_disclosures_load_runtime"

# ---------------------------------------------------------------------------
# Shared factory helpers
# ---------------------------------------------------------------------------

_LOCAL_ROOT = Path("/tmp/test_artifacts")


def _parse_result(succeeded: int = 1, failed: int = 0) -> MagicMock:
    r = MagicMock()
    r.succeeded_count = succeeded
    r.failed_count = failed
    r.processed_count = succeeded + failed
    sessions = tuple(MagicMock() for _ in range(succeeded))
    r.parse_sessions = sessions
    return r


def _transform_results(n: int = 1) -> list[MagicMock]:
    return [MagicMock() for _ in range(n)]


def _load_result() -> MagicMock:
    r = MagicMock()
    r.run_id = 42
    return r


def _patch_all(
    parse_result=None,
    transform_results=None,
    load_result=None,
):
    """Context manager that patches all three boundaries simultaneously."""
    import contextlib

    pr = parse_result if parse_result is not None else _parse_result()
    tr = transform_results if transform_results is not None else _transform_results()
    lr = load_result if load_result is not None else _load_result()

    @contextlib.contextmanager
    def _ctx():
        with (
            patch(_PARSE, return_value=pr) as mock_parse,
            patch(_TRANSFORM, return_value=tr) as mock_transform,
            patch(_LOAD, return_value=lr) as mock_load,
        ):
            yield {"parse": mock_parse, "transform": mock_transform, "load": mock_load}

    return _ctx()


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_returns_typed_result(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert isinstance(result, DisclosuresParseLoadResult)

    def test_result_exposes_parse_result(self):
        conn = MagicMock()
        pr = _parse_result()
        with _patch_all(parse_result=pr):
            result = run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.parse_result is pr

    def test_result_exposes_transform_count(self):
        conn = MagicMock()
        tr = _transform_results(3)
        with _patch_all(transform_results=tr):
            result = run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.transform_count == 3

    def test_result_exposes_load_result(self):
        conn = MagicMock()
        lr = _load_result()
        with _patch_all(load_result=lr):
            result = run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.load_result is lr

    def test_transform_count_zero_when_no_transform_results(self):
        conn = MagicMock()
        with _patch_all(transform_results=[]):
            result = run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.transform_count == 0

    def test_result_exposes_skipped_transform_count(self):
        conn = MagicMock()
        with _patch_all():
            result = run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert hasattr(result, "skipped_transform_count")


# ---------------------------------------------------------------------------
# Step ordering — parse → transform → load
# ---------------------------------------------------------------------------


class TestStepOrdering:
    @staticmethod
    def _run_with_call_order() -> list[str]:
        """Run the pipeline and return the order each step was invoked."""
        call_order: list[str] = []
        pr, tr, lr = _parse_result(), _transform_results(), _load_result()
        with (
            patch(_PARSE, side_effect=lambda *a, **k: call_order.append("parse") or pr),
            patch(_TRANSFORM, side_effect=lambda *a, **k: call_order.append("transform") or tr),
            patch(_LOAD, side_effect=lambda *a, **k: call_order.append("load") or lr),
        ):
            run_disclosures_parse_load_runtime(MagicMock(), local_root=_LOCAL_ROOT)
        return call_order

    def test_parse_called_before_transform(self):
        order = self._run_with_call_order()
        assert order.index("parse") < order.index("transform")

    def test_transform_called_before_load(self):
        order = self._run_with_call_order()
        assert order.index("transform") < order.index("load")

    def test_all_three_called_exactly_once(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        mocks["parse"].assert_called_once()
        mocks["transform"].assert_called_once()
        mocks["load"].assert_called_once()


# ---------------------------------------------------------------------------
# Parse wiring — arguments forwarded to run_disclosure_parse_runtime
# ---------------------------------------------------------------------------


class TestParseWiring:
    def test_conn_forwarded_to_parse(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert mocks["parse"].call_args[0][0] is conn

    def test_local_root_forwarded_to_parse(self):
        conn = MagicMock()
        root = Path("/data/disclosures")
        with _patch_all() as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=root)
        _, kwargs = mocks["parse"].call_args
        assert kwargs["local_root"] == root

    def test_chamber_forwarded_to_parse(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT, chamber="senate")
        _, kwargs = mocks["parse"].call_args
        assert kwargs["chamber"] == "senate"

    def test_chamber_defaults_to_none(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        _, kwargs = mocks["parse"].call_args
        assert kwargs.get("chamber") is None

    def test_limit_forwarded_to_parse(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT, limit=5)
        _, kwargs = mocks["parse"].call_args
        assert kwargs["limit"] == 5

    def test_limit_defaults_to_none(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        _, kwargs = mocks["parse"].call_args
        assert kwargs.get("limit") is None

    def test_parser_name_forwarded(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_parse_load_runtime(
                conn, local_root=_LOCAL_ROOT, parser_name="house_ocr_v2"
            )
        _, kwargs = mocks["parse"].call_args
        assert kwargs["parser_name"] == "house_ocr_v2"

    def test_parser_version_forwarded(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_parse_load_runtime(
                conn, local_root=_LOCAL_ROOT, parser_version="2"
            )
        _, kwargs = mocks["parse"].call_args
        assert kwargs["parser_version"] == "2"

    def test_parser_name_default(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        _, kwargs = mocks["parse"].call_args
        assert kwargs["parser_name"] == "text_extract_v1"

    def test_parser_version_default(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        _, kwargs = mocks["parse"].call_args
        assert kwargs["parser_version"] == "1"


# ---------------------------------------------------------------------------
# Transform wiring — parse_sessions forwarded to transform_parse_sessions
# ---------------------------------------------------------------------------


class TestTransformWiring:
    def test_conn_forwarded_to_transform(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert mocks["transform"].call_args[0][0] is conn

    def test_parse_sessions_forwarded_to_transform(self):
        conn = MagicMock()
        pr = _parse_result(succeeded=2)
        with _patch_all(parse_result=pr) as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert mocks["transform"].call_args[0][1] is pr.parse_sessions

    def test_empty_parse_sessions_forwarded_when_all_failed(self):
        conn = MagicMock()
        pr = _parse_result(succeeded=0, failed=3)
        with _patch_all(parse_result=pr) as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert mocks["transform"].call_args[0][1] == ()


# ---------------------------------------------------------------------------
# Load wiring — transform results forwarded to run_disclosures_load_runtime
# ---------------------------------------------------------------------------


class TestLoadWiring:
    def test_conn_forwarded_to_load(self):
        conn = MagicMock()
        with _patch_all() as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert mocks["load"].call_args[0][0] is conn

    def test_transform_results_forwarded_to_load(self):
        conn = MagicMock()
        tr = _transform_results(2)
        with _patch_all(transform_results=tr) as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert mocks["load"].call_args[0][1] is tr

    def test_empty_transform_results_forwarded(self):
        conn = MagicMock()
        with _patch_all(transform_results=[]) as mocks:
            run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        args, _ = mocks["load"].call_args
        assert args[1] == []


# ---------------------------------------------------------------------------
# transform_count reflects actual transform output length
# ---------------------------------------------------------------------------


class TestTransformCount:
    def test_transform_count_matches_transform_output_length(self):
        conn = MagicMock()
        for n in (0, 1, 3, 7):
            with _patch_all(transform_results=_transform_results(n)):
                result = run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
            assert result.transform_count == n, f"expected {n}, got {result.transform_count}"

    def test_transform_count_independent_of_parse_succeeded_count(self):
        """transform_count reflects what transform returns, not parse.succeeded_count."""
        conn = MagicMock()
        pr = _parse_result(succeeded=5)
        tr = _transform_results(2)  # transform may filter or fail some sessions
        with _patch_all(parse_result=pr, transform_results=tr):
            result = run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.transform_count == 2


# ---------------------------------------------------------------------------
# skipped_transform_count — sessions that succeeded parse but not transform
# ---------------------------------------------------------------------------


class TestSkippedTransformCount:
    def test_skipped_count_zero_when_all_sessions_transform(self):
        conn = MagicMock()
        pr = _parse_result(succeeded=3)
        tr = _transform_results(3)
        with _patch_all(parse_result=pr, transform_results=tr):
            result = run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.skipped_transform_count == 0

    def test_skipped_count_equals_gap_between_succeeded_and_transformed(self):
        conn = MagicMock()
        pr = _parse_result(succeeded=5)
        tr = _transform_results(2)
        with _patch_all(parse_result=pr, transform_results=tr):
            result = run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.skipped_transform_count == 3

    def test_skipped_count_equals_succeeded_when_nothing_transforms(self):
        conn = MagicMock()
        pr = _parse_result(succeeded=4)
        with _patch_all(parse_result=pr, transform_results=[]):
            result = run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.skipped_transform_count == 4

    def test_skipped_count_zero_when_no_sessions(self):
        conn = MagicMock()
        pr = _parse_result(succeeded=0, failed=0)
        with _patch_all(parse_result=pr, transform_results=[]):
            result = run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
        assert result.skipped_transform_count == 0

    def test_transform_plus_skipped_equals_succeeded(self):
        conn = MagicMock()
        for succeeded, transformed in ((0, 0), (1, 0), (1, 1), (4, 3), (7, 7)):
            pr = _parse_result(succeeded=succeeded)
            tr = _transform_results(transformed)
            with _patch_all(parse_result=pr, transform_results=tr):
                result = run_disclosures_parse_load_runtime(conn, local_root=_LOCAL_ROOT)
            assert result.transform_count + result.skipped_transform_count == pr.succeeded_count
