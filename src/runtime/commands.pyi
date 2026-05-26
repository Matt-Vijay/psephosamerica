from __future__ import annotations

from collections.abc import Callable
import datetime as dt
from pathlib import Path
from typing import Any

from src.parse.disclosures.transform import DisclosureTransformResult
from src.pipeline.publish_snapshot_run import ZipBundleInputs
from src.prediction.backtest import PredictionBacktestPayload
from src.prediction.eval_report import PredictionEvalReportPayload
from src.prediction.input_inventory import PredictionInputInventoryPayload
from src.prediction.window_plan import PredictionEvalWindowPlanPayload
from src.runtime.app import OpenPactRuntime
from src.runtime.congress import CongressLoadResult
from src.runtime.congress_archive_materialize import MaterializedCongressArchiveResult
from src.runtime.congress_options import CongressLoadOptions
from src.runtime.context import RuntimeContext
from src.runtime.disclosures import DisclosuresLoadRuntimeResult
from src.runtime.disclosures_bundle import DisclosuresBundle
from src.runtime.disclosures_bundle_materialize import MaterializedDisclosuresBundleResult
from src.runtime.disclosures_bundle_process import DisclosuresBundleProcessResult
from src.runtime.fec import FecBulkFilePaths, FecLocalLoadResult
from src.runtime.member_fec_crosswalk import MemberFecCrosswalkLoadResult
from src.runtime.history_backfill import LocalHistoryBackfillResult
from src.runtime.history_verify_types import HistoryVerifyResult
from src.runtime.oracle_contracts import LocalOracleOptions, LocalOracleRunResult
from src.runtime.publish import PublishRuntimeResult
from src.runtime.publish_roundtrip_types import PublishRoundtripResult
from src.runtime.publish_verify_types import PublishVerifyResult
from src.runtime.recompute import RuntimeRecomputeResult

def build_runtime(
    settings: Any | None = ...,
    *,
    data_root: Path | None = ...,
    publish_root: Path | None = ...,
    connect_fn: Callable[[Any], Any] | None = ...,
) -> OpenPactRuntime: ...
def load_congress(ctx: RuntimeContext, options: CongressLoadOptions) -> CongressLoadResult: ...
def load_congress_local(
    ctx: RuntimeContext,
    archive: Path,
    options: CongressLoadOptions,
) -> CongressLoadResult: ...
def load_disclosures(
    ctx: RuntimeContext,
    results: list[DisclosureTransformResult],
) -> DisclosuresLoadRuntimeResult: ...
def load_fec_local(ctx: RuntimeContext, files: FecBulkFilePaths) -> FecLocalLoadResult: ...
def load_member_fec_crosswalk_local(
    ctx: RuntimeContext,
    crosswalk: Path,
    *,
    source_url: str | None = ...,
) -> MemberFecCrosswalkLoadResult: ...
def process_disclosures_local(
    ctx: RuntimeContext,
    bundle: DisclosuresBundle,
    *,
    local_root: Path | None = ...,
) -> DisclosuresBundleProcessResult: ...
def recompute_snapshot(
    ctx: RuntimeContext,
    snapshot_date: dt.date,
    *,
    statement_rows: list[dict[str, Any]] | None = ...,
    statement_rows_source: dict[str, Any] | None = ...,
) -> RuntimeRecomputeResult: ...
def publish_snapshot(
    ctx: RuntimeContext,
    snapshot_date: dt.date,
    target_dir: Path,
    zip_bundle_inputs: ZipBundleInputs,
) -> PublishRuntimeResult: ...
def verify_publish_local(publish_root: Path) -> PublishVerifyResult: ...
def verify_publish_roundtrip_local(
    ctx: RuntimeContext,
    publish_root: Path,
) -> PublishRoundtripResult: ...
def verify_history_aggregate_local(publish_root: Path) -> HistoryVerifyResult: ...
def load_disclosures_bundle(path: Path) -> DisclosuresBundle: ...
def run_oracle_local_command(
    ctx: RuntimeContext,
    congress_archive: Path,
    disclosures_bundle: DisclosuresBundle,
    options: LocalOracleOptions,
) -> LocalOracleRunResult: ...
def run_history_backfill_local_command(
    ctx: RuntimeContext,
    congress_archive: Path,
    disclosures_bundle: DisclosuresBundle,
    *,
    congress: int,
    target_root: Path,
    aggregate_root: Path | None = ...,
    chamber: str | None = ...,
    limit: int | None = ...,
    start_date: dt.date | None = ...,
    end_date: dt.date | None = ...,
    overwrite: bool = ...,
    continue_on_error: bool = ...,
    artifact_root: Path | None = ...,
    input_readiness: Any | None = ...,
) -> LocalHistoryBackfillResult: ...
def run_prediction_backtest_command(
    ctx: RuntimeContext,
    *,
    feature_cutoff: dt.date,
    label_start: dt.date,
    label_end: dt.date,
    model: str = ...,
    bill_semantics_root: Path | None = ...,
) -> PredictionBacktestPayload: ...
def run_prediction_eval_report_command(
    ctx: RuntimeContext,
    *,
    training_feature_cutoff: dt.date,
    train_start: dt.date,
    train_end: dt.date,
    feature_cutoff: dt.date,
    label_start: dt.date,
    label_end: dt.date,
    bill_semantics_root: Path | None = ...,
) -> PredictionEvalReportPayload: ...
def run_prediction_eval_window_plan_command(
    *,
    start_label_year: int,
    end_label_year: int,
    train_years: int = ...,
    label_years: int = ...,
    max_feature_cutoff: dt.date | None = ...,
    output_dir: str = ...,
    bill_semantics_root: str | None = ...,
    congress_archive_manifest: str | None = ...,
    plan_artifact_path: str | None = ...,
) -> PredictionEvalWindowPlanPayload: ...
def run_prediction_input_inventory_command(
    ctx: RuntimeContext,
    *,
    training_feature_cutoff: dt.date,
    train_start: dt.date,
    train_end: dt.date,
    feature_cutoff: dt.date,
    label_start: dt.date,
    label_end: dt.date,
) -> PredictionInputInventoryPayload: ...
def summarize_local_oracle_run_result(result: LocalOracleRunResult) -> dict[str, Any]: ...
def summarize_local_history_backfill_result(
    result: LocalHistoryBackfillResult,
) -> dict[str, Any]: ...
def _oracle_summary_ok(summary: dict[str, Any]) -> bool: ...
def dispatch_command(args: Any) -> dict[str, Any]: ...
def manifest_from_existing_archive(archive: Any) -> Any: ...
def validate_congress_archive_manifest(
    manifest: Any, *, exists: Callable[[Path], bool] = ...
) -> Any: ...
def write_manifest(manifest_path: Path, manifest: Any) -> Path: ...
def materialize_congress_archive(
    *,
    api_key: str,
    archive_root: Path,
    congress: int,
    include_votes: bool = ...,
    house_vote_year: int | None = ...,
    senate_session: int | None = ...,
    manifest_path: Path | None = ...,
) -> MaterializedCongressArchiveResult: ...
def materialize_disclosures_bundle(
    *,
    years: list[int],
    chamber: str,
    bundle_path: Path,
    artifact_root: Path,
) -> MaterializedDisclosuresBundleResult: ...

COMMAND_REGISTRY: dict[str, Callable[[Any], dict[str, Any]]]
