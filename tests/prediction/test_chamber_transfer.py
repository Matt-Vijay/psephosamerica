"""Tests for cross-chamber / cross-corpus defection transfer."""

from __future__ import annotations

from datetime import date

from src.prediction.chamber_transfer import transfer_report
from src.runtime.cross_pressured_experiment import VoteRecord


def _record(
    member: str, *, is_yea: bool, party_lean_yea: bool, sectors: tuple[str, ...], day: int
) -> VoteRecord:
    return VoteRecord(
        member=member,
        party="R",
        state="TX",
        vote_date=date(2023, 1, day),
        is_yea=is_yea,
        party_alignment=1.0 if party_lean_yea else -1.0,
        sectors=sectors,
        is_cross_pressured=is_yea != party_lean_yea,
    )


def _chamber(prefix: str, *, defector_breaks: bool) -> list[VoteRecord]:
    out: list[VoteRecord] = []
    for day in range(1, 21):
        # one defector who breaks on energy, two loyalists
        out.append(
            _record(
                f"{prefix}_D",
                is_yea=defector_breaks,
                party_lean_yea=False,
                sectors=("energy_utilities",),
                day=day,
            )
        )
        out.append(
            _record(
                f"{prefix}_L1",
                is_yea=False,
                party_lean_yea=False,
                sectors=("energy_utilities",),
                day=day,
            )
        )
        out.append(
            _record(
                f"{prefix}_L2",
                is_yea=False,
                party_lean_yea=False,
                sectors=("energy_utilities",),
                day=day,
            )
        )
    return out


def test_transfer_report_has_three_points_and_gap() -> None:
    source = _chamber("H", defector_breaks=True)
    target_train = _chamber("S", defector_breaks=True)
    target_eval = _chamber("S", defector_breaks=True)
    report = transfer_report(
        source, target_train, target_eval, source_label="house", target_label="senate"
    )
    assert report.source_label == "house" and report.target_label == "senate"
    for auc in (report.zero_shot_auc, report.target_only_auc, report.joint_auc):
        assert 0.0 <= auc <= 1.0
    # the same loyalty/divergence signal exists in both chambers -> zero-shot transfers
    assert report.zero_shot_auc > 0.6
    assert report.gap == report.target_only_auc - report.zero_shot_auc
    assert report.target_eval_pairs == len(target_eval)


def test_transfer_report_is_jsonable() -> None:
    source = _chamber("H", defector_breaks=True)
    target = _chamber("S", defector_breaks=True)
    report = transfer_report(source, target, target)
    payload = report.as_dict()
    assert set(payload) >= {"zero_shot_auc", "target_only_auc", "joint_auc", "gap"}
