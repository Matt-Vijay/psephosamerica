from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.prediction.source_url_audit import build_prediction_source_url_audit


def test_build_prediction_source_url_audit_reports_split_signal_gaps(
    tmp_path: Path,
) -> None:
    report = tmp_path / "eval-report.json"
    report.write_text(
        json.dumps(
            {
                "training_feature_source_coverage": [
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "bill_issue_sector_overlap",
                        "prediction_count": 3,
                        "url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 1 / 3,
                    }
                ],
                "evaluation_feature_source_coverage": [
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "committee_jurisdiction_overlap",
                        "prediction_count": 2,
                        "url_sourced_prediction_count": 0,
                        "url_source_coverage_rate": 0.0,
                    },
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 2,
                        "url_sourced_prediction_count": 2,
                        "url_source_coverage_rate": 1.0,
                    },
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "financial_sector_overlap",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 1.0,
                        "official_source_sourced_prediction_count": 0,
                        "official_source_coverage_rate": 0.0,
                    },
                ],
                "dataset": {
                    "training": {
                        "examples": [
                            {
                                "vote_event_id": 10,
                                "event_key": "house-118-1-10",
                                "jurisdiction_id": "us_congress",
                                "legislative_body_id": "us_congress_house",
                                "legislative_session_id": "congress_118_session_1",
                                "bill_key": "118-hr-1",
                                "member_bioguide_id": "A000001",
                                "features": {
                                    "bill_issue_sector_overlap": 1.0,
                                },
                                "feature_source_anchors": {
                                    "bill_issue_sector_overlap": [
                                        {
                                            "source_type": "congress_bill",
                                            "source_id": "118-hr-1",
                                            "url": None,
                                            "label": "H.R. 1",
                                        }
                                    ],
                                },
                            },
                            {
                                "vote_event_id": 10,
                                "event_key": "house-118-1-10",
                                "jurisdiction_id": "us_congress",
                                "legislative_body_id": "us_congress_house",
                                "legislative_session_id": "congress_118_session_1",
                                "bill_key": "118-hr-1",
                                "member_bioguide_id": "B000002",
                                "features": {
                                    "bill_issue_sector_overlap": 0.5,
                                },
                                "feature_source_anchors": {
                                    "bill_issue_sector_overlap": [
                                        {
                                            "source_type": "congress_bill",
                                            "source_id": "118-hr-1",
                                            "url": None,
                                            "label": "H.R. 1",
                                        }
                                    ],
                                },
                            },
                        ],
                    },
                },
                "comparisons": [
                    {
                        "vote_event_id": 20,
                        "event_key": "assembly-2025-1-20",
                        "jurisdiction_id": "state_ca",
                        "legislative_body_id": "ca_assembly",
                        "legislative_session_id": "2025_regular",
                        "bill_key": "119-hr-2",
                        "bill_context_key": "state_ca:119-hr-2",
                        "member_bioguide_id": "B000002",
                        "feature_signals_by_model": {
                            "ontology_signal_model": {
                                "committee_jurisdiction_overlap": 1.0,
                                "financial_sector_overlap": 1.0,
                            },
                        },
                        "feature_source_anchors_by_model": {
                            "ontology_signal_model": {
                                "committee_jurisdiction_overlap": [
                                    {
                                        "source_type": "committee_membership",
                                        "source_id": "HSEC",
                                        "url": None,
                                        "label": "Energy Committee",
                                    }
                                ],
                                "financial_sector_overlap": [
                                    {
                                        "source_type": "financial_disclosure",
                                        "source_id": "fd-1",
                                        "url": "https://example.com/fd.pdf",
                                        "label": "Unofficial disclosure mirror",
                                    }
                                ],
                            },
                        },
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = build_prediction_source_url_audit(report)

    assert result.eval_report_path == str(report)
    assert result.eval_report_sha256 == hashlib.sha256(report.read_bytes()).hexdigest()
    assert result.checked == 1
    assert result.gap_count == 3
    assert result.jurisdiction_count == 2
    assert result.implemented_jurisdiction_count == 1
    assert result.portable_jurisdiction_count == 1
    assert result.portable_sample_missing_body_id_count == 0
    assert result.portable_sample_missing_session_id_count == 0
    assert result.legislative_body_count == 2
    assert result.legislative_session_count == 2
    assert result.jurisdiction_ids == ("state_ca", "us_congress")
    assert result.implemented_jurisdiction_ids == ("us_congress",)
    assert result.portable_jurisdiction_ids == ("state_ca",)
    assert result.legislative_body_ids == (
        "state_ca:ca_assembly",
        "us_congress:us_congress_house",
    )
    assert result.legislative_session_ids == (
        "state_ca:ca_assembly:2025_regular",
        "us_congress:us_congress_house:congress_118_session_1",
    )
    assert result.source_family_count == 3
    assert result.source_family_ids == (
        "committee_membership",
        "congress_bill",
        "financial_disclosure",
    )
    assert result.missing_url_sourced_prediction_count == 4
    assert result.missing_official_source_prediction_count == 5
    assert [(gap.split, gap.signal_name) for gap in result.gaps] == [
        ("evaluation", "committee_jurisdiction_overlap"),
        ("training", "bill_issue_sector_overlap"),
        ("evaluation", "financial_sector_overlap"),
    ]
    assert result.gaps[0].sample_vote_event_ids == (20,)
    assert result.gaps[0].source_family_ids == ("committee_membership",)
    assert result.gaps[0].sample_bill_keys == ("state_ca:119-hr-2",)
    assert result.gaps[0].sample_member_bioguide_ids == ("B000002",)
    assert result.gaps[0].sample_cases == (
        {
            "vote_event_id": 20,
            "event_key": "assembly-2025-1-20",
            "jurisdiction_id": "state_ca",
            "legislative_body_id": "ca_assembly",
            "legislative_session_id": "2025_regular",
            "bill_key": "119-hr-2",
            "bill_context_key": "state_ca:119-hr-2",
            "member_bioguide_id": "B000002",
        },
    )
    assert result.gaps[1].sample_vote_event_ids == (10,)
    assert result.gaps[1].source_family_ids == ("congress_bill",)
    assert result.gaps[1].sample_bill_keys == ("118-hr-1",)
    assert result.gaps[1].sample_member_bioguide_ids == ("A000001", "B000002")
    assert result.gaps[1].sample_cases == (
        {
            "vote_event_id": 10,
            "event_key": "house-118-1-10",
            "jurisdiction_id": "us_congress",
            "legislative_body_id": "us_congress_house",
            "legislative_session_id": "congress_118_session_1",
            "bill_key": "118-hr-1",
            "member_bioguide_id": "A000001",
        },
        {
            "vote_event_id": 10,
            "event_key": "house-118-1-10",
            "jurisdiction_id": "us_congress",
            "legislative_body_id": "us_congress_house",
            "legislative_session_id": "congress_118_session_1",
            "bill_key": "118-hr-1",
            "member_bioguide_id": "B000002",
        },
    )
    assert result.gaps[2].missing_url_sourced_prediction_count == 0
    assert result.gaps[2].source_family_ids == ("financial_disclosure",)
    assert result.gaps[2].missing_official_source_prediction_count == 1
    assert result.gaps[2].official_source_coverage_rate == 0.0


def test_build_prediction_source_url_audit_counts_portable_missing_context(
    tmp_path: Path,
) -> None:
    report = tmp_path / "eval-report.json"
    report.write_text(
        json.dumps(
            {
                "evaluation_feature_source_coverage": [
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 2,
                        "url_sourced_prediction_count": 0,
                        "url_source_coverage_rate": 0.0,
                    }
                ],
                "dataset": {
                    "evaluation": {
                        "examples": [
                            {
                                "vote_event_id": 20,
                                "event_key": "assembly-2025-1-20",
                                "jurisdiction_id": "state_ca",
                                "bill_key": "state_ca-2025-ab-1",
                                "member_bioguide_id": "B000002",
                                "features": {"member_vote_history": 1.0},
                                "feature_source_anchors": {
                                    "member_vote_history": [
                                        {
                                            "source_type": "congress_vote",
                                            "source_id": "state-ca-vote-20",
                                        }
                                    ]
                                },
                            },
                            {
                                "vote_event_id": 21,
                                "event_key": "assembly-2025-1-21",
                                "jurisdiction_id": "state_ca",
                                "legislative_body_id": "ca_assembly",
                                "bill_key": "state_ca-2025-ab-2",
                                "member_bioguide_id": "B000003",
                                "features": {"member_vote_history": 1.0},
                                "feature_source_anchors": {
                                    "member_vote_history": [
                                        {
                                            "source_type": "congress_vote",
                                            "source_id": "state-ca-vote-21",
                                        }
                                    ]
                                },
                            },
                        ]
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = build_prediction_source_url_audit(report)

    assert result.portable_jurisdiction_count == 1
    assert result.portable_sample_missing_body_id_count == 1
    assert result.portable_sample_missing_session_id_count == 1
    assert result.legislative_body_ids == ("state_ca:ca_assembly",)
    assert result.legislative_session_ids == ()
    assert result.source_family_ids == ("legislative_vote",)


def test_build_prediction_source_url_audit_does_not_sample_boolean_vote_ids(
    tmp_path: Path,
) -> None:
    report = tmp_path / "eval-report.json"
    report.write_text(
        json.dumps(
            {
                "evaluation_feature_source_coverage": [
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "committee_jurisdiction_overlap",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 0,
                        "url_source_coverage_rate": 0.0,
                    },
                ],
                "comparisons": [
                    {
                        "vote_event_id": True,
                        "bill_key": "119-hr-2",
                        "member_bioguide_id": "B000002",
                        "feature_signals_by_model": {
                            "ontology_signal_model": {
                                "committee_jurisdiction_overlap": 1.0,
                            },
                        },
                        "feature_source_anchors_by_model": {
                            "ontology_signal_model": {
                                "committee_jurisdiction_overlap": [],
                            },
                        },
                    },
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = build_prediction_source_url_audit(report)

    assert result.gaps[0].sample_vote_event_ids == ()
    assert result.gaps[0].sample_cases == (
        {
            "bill_key": "119-hr-2",
            "member_bioguide_id": "B000002",
        },
    )


def test_build_prediction_source_url_audit_does_not_sample_non_positive_vote_ids(
    tmp_path: Path,
) -> None:
    report = tmp_path / "eval-report.json"
    report.write_text(
        json.dumps(
            {
                "evaluation_feature_source_coverage": [
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 2,
                        "url_sourced_prediction_count": 0,
                        "url_source_coverage_rate": 0.0,
                    },
                ],
                "comparisons": [
                    {
                        "vote_event_id": 0,
                        "bill_key": "119-hr-2",
                        "member_bioguide_id": "B000002",
                        "feature_signals_by_model": {
                            "ontology_signal_model": {"member_vote_history": 0.8},
                        },
                        "feature_source_anchors_by_model": {
                            "ontology_signal_model": {"member_vote_history": []},
                        },
                    },
                    {
                        "vote_event_id": -7,
                        "bill_key": "119-hr-3",
                        "member_bioguide_id": "B000003",
                        "feature_signals_by_model": {
                            "ontology_signal_model": {"member_vote_history": 0.2},
                        },
                        "feature_source_anchors_by_model": {
                            "ontology_signal_model": {"member_vote_history": []},
                        },
                    },
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = build_prediction_source_url_audit(report)

    assert result.gaps[0].sample_vote_event_ids == ()
    assert result.gaps[0].sample_cases == (
        {
            "bill_key": "119-hr-2",
            "member_bioguide_id": "B000002",
        },
        {
            "bill_key": "119-hr-3",
            "member_bioguide_id": "B000003",
        },
    )


def test_build_prediction_source_url_audit_rejects_non_object(
    tmp_path: Path,
) -> None:
    report = tmp_path / "eval-report.json"
    report.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="must be an object"):
        build_prediction_source_url_audit(report)


def test_build_prediction_source_url_audit_rejects_impossible_coverage_counts(
    tmp_path: Path,
) -> None:
    report = tmp_path / "eval-report.json"
    report.write_text(
        json.dumps(
            {
                "evaluation_feature_source_coverage": [
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "financial_sector_overlap",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 2,
                        "url_source_coverage_rate": 2.0,
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="url_sourced_prediction_count cannot exceed"):
        build_prediction_source_url_audit(report)


def test_build_prediction_source_url_audit_rejects_mismatched_coverage_rates(
    tmp_path: Path,
) -> None:
    report = tmp_path / "eval-report.json"
    report.write_text(
        json.dumps(
            {
                "evaluation_feature_source_coverage": [
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "financial_sector_overlap",
                        "prediction_count": 4,
                        "url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 0.5,
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="url_source_coverage_rate must match"):
        build_prediction_source_url_audit(report)


def test_build_prediction_source_url_audit_rejects_blank_coverage_identity(
    tmp_path: Path,
) -> None:
    report = tmp_path / "eval-report.json"
    report.write_text(
        json.dumps(
            {
                "evaluation_feature_source_coverage": [
                    {
                        "model_name": " ",
                        "signal_name": "financial_sector_overlap",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 0,
                        "url_source_coverage_rate": 0.0,
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="model_name must be nonblank"):
        build_prediction_source_url_audit(report)


def test_build_prediction_source_url_audit_normalizes_sample_identifiers(
    tmp_path: Path,
) -> None:
    report = tmp_path / "eval-report.json"
    report.write_text(
        json.dumps(
            {
                "evaluation_feature_source_coverage": [
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "financial_sector_overlap",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 0,
                        "url_source_coverage_rate": 0.0,
                    }
                ],
                "comparisons": [
                    {
                        "vote_event_id": 20,
                        "bill_key": " 119-hr-2 ",
                        "member_bioguide_id": " B000002 ",
                        "feature_signals_by_model": {
                            "ontology_signal_model": {
                                "financial_sector_overlap": 1.0,
                            },
                        },
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = build_prediction_source_url_audit(report)

    assert result.gaps[0].sample_bill_keys == ("119-hr-2",)
    assert result.gaps[0].sample_member_bioguide_ids == ("B000002",)
    assert result.gaps[0].sample_cases == (
        {
            "vote_event_id": 20,
            "bill_key": "119-hr-2",
            "member_bioguide_id": "B000002",
        },
    )


def test_build_prediction_source_url_audit_keeps_state_vote_source_family_portable(
    tmp_path: Path,
) -> None:
    report = tmp_path / "eval-report.json"
    report.write_text(
        json.dumps(
            {
                "evaluation_feature_source_coverage": [
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 0,
                        "url_source_coverage_rate": 0.0,
                    }
                ],
                "comparisons": [
                    {
                        "vote_event_id": 20,
                        "event_key": "assembly-2025-1-20",
                        "jurisdiction_id": "state_ca",
                        "legislative_body_id": "ca_assembly",
                        "legislative_session_id": "2025_regular",
                        "bill_key": "state_ca:2025:ab:1",
                        "member_bioguide_id": "B000002",
                        "feature_signals_by_model": {
                            "ontology_signal_model": {
                                "member_vote_history": 1.0,
                            },
                        },
                        "feature_source_anchors_by_model": {
                            "ontology_signal_model": {
                                "member_vote_history": [
                                    {
                                        "source_type": "vote_event",
                                        "source_id": "state-ca-2025-20",
                                        "url": None,
                                        "label": "Assembly vote 20",
                                    }
                                ],
                            },
                        },
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = build_prediction_source_url_audit(report)

    assert result.source_family_ids == ("legislative_vote",)
    assert result.gaps[0].source_family_ids == ("legislative_vote",)


def test_build_prediction_source_url_audit_preserves_clean_portable_context(
    tmp_path: Path,
) -> None:
    report = tmp_path / "eval-report.json"
    report.write_text(
        json.dumps(
            {
                "evaluation_feature_source_coverage": [
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 1.0,
                        "official_source_sourced_prediction_count": 1,
                        "official_source_coverage_rate": 1.0,
                    }
                ],
                "comparisons": [
                    {
                        "vote_event_id": 20,
                        "event_key": "assembly-2025-1-20",
                        "jurisdiction_id": "state_ca",
                        "legislative_body_id": "ca_assembly",
                        "legislative_session_id": "2025_regular",
                        "bill_key": "state_ca:2025:ab:1",
                        "member_bioguide_id": "B000002",
                        "feature_signals_by_model": {
                            "ontology_signal_model": {
                                "member_vote_history": 1.0,
                            },
                        },
                        "feature_source_anchors_by_model": {
                            "ontology_signal_model": {
                                "member_vote_history": [
                                    {
                                        "source_type": "congress_vote",
                                        "source_id": "state-ca-2025-20",
                                        "url": (
                                            "https://leginfo.legislature.ca.gov/"
                                            "faces/billVotesClient.xhtml?bill_id=202520260AB1"
                                        ),
                                    }
                                ],
                            },
                        },
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = build_prediction_source_url_audit(report)

    assert result.gap_count == 0
    assert result.jurisdiction_ids == ("state_ca",)
    assert result.portable_jurisdiction_ids == ("state_ca",)
    assert result.legislative_body_ids == ("state_ca:ca_assembly",)
    assert result.legislative_session_ids == ("state_ca:ca_assembly:2025_regular",)
    assert result.portable_sample_missing_body_id_count == 0
    assert result.portable_sample_missing_session_id_count == 0
    assert result.source_family_ids == ("legislative_vote",)


def test_build_prediction_source_url_audit_normalizes_state_congress_vote_aliases(
    tmp_path: Path,
) -> None:
    report = tmp_path / "eval-report.json"
    report.write_text(
        json.dumps(
            {
                "evaluation_feature_source_coverage": [
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 0,
                        "url_source_coverage_rate": 0.0,
                    }
                ],
                "comparisons": [
                    {
                        "vote_event_id": 20,
                        "event_key": "assembly-2025-1-20",
                        "jurisdiction_id": "state_ca",
                        "legislative_body_id": "ca_assembly",
                        "legislative_session_id": "2025_regular",
                        "bill_key": "state_ca:2025:ab:1",
                        "member_bioguide_id": "B000002",
                        "feature_signals_by_model": {
                            "ontology_signal_model": {
                                "member_vote_history": 1.0,
                            },
                        },
                        "feature_source_anchors_by_model": {
                            "ontology_signal_model": {
                                "member_vote_history": [
                                    {
                                        "source_type": "congress_vote",
                                        "source_id": "state-ca-2025-20",
                                        "url": None,
                                        "label": "Assembly vote 20",
                                    }
                                ],
                            },
                        },
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = build_prediction_source_url_audit(report)

    assert result.source_family_ids == ("legislative_vote",)
    assert result.gaps[0].source_family_ids == ("legislative_vote",)


def test_build_prediction_source_url_audit_requires_official_congress_vote_alias_url(
    tmp_path: Path,
) -> None:
    report = tmp_path / "eval-report.json"
    report.write_text(
        json.dumps(
            {
                "evaluation_feature_source_coverage": [
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 1.0,
                        "official_source_sourced_prediction_count": 0,
                        "official_source_coverage_rate": 0.0,
                    }
                ],
                "comparisons": [
                    {
                        "vote_event_id": 20,
                        "event_key": "house-119-1-20",
                        "jurisdiction_id": "us_congress",
                        "legislative_body_id": "us_congress_house",
                        "legislative_session_id": "congress_119_session_1",
                        "bill_key": "119-hr-1",
                        "member_bioguide_id": "B000002",
                        "feature_signals_by_model": {
                            "ontology_signal_model": {
                                "member_vote_history": 1.0,
                            },
                        },
                        "feature_source_anchors_by_model": {
                            "ontology_signal_model": {
                                "member_vote_history": [
                                    {
                                        "source_type": "congress_vote",
                                        "source_id": "house-119-1-20",
                                        "url": "https://example.invalid/vote/119/20",
                                    }
                                ],
                            },
                        },
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = build_prediction_source_url_audit(report)

    assert result.missing_url_sourced_prediction_count == 0
    assert result.missing_official_source_prediction_count == 1
    assert result.source_family_ids == ("congress_vote",)
    assert result.gaps[0].source_family_ids == ("congress_vote",)


def test_build_prediction_source_url_audit_treats_state_vote_alias_official_url_as_legislative(
    tmp_path: Path,
) -> None:
    report = tmp_path / "eval-report.json"
    report.write_text(
        json.dumps(
            {
                "evaluation_feature_source_coverage": [
                    {
                        "model_name": "ontology_signal_model",
                        "signal_name": "member_vote_history",
                        "prediction_count": 1,
                        "url_sourced_prediction_count": 1,
                        "url_source_coverage_rate": 1.0,
                        "official_source_sourced_prediction_count": 0,
                        "official_source_coverage_rate": 0.0,
                    }
                ],
                "comparisons": [
                    {
                        "vote_event_id": 20,
                        "event_key": "assembly-2025-1-20",
                        "jurisdiction_id": "state_ca",
                        "legislative_body_id": "ca_assembly",
                        "legislative_session_id": "2025_regular",
                        "bill_key": "state_ca:2025:ab:1",
                        "member_bioguide_id": "B000002",
                        "feature_signals_by_model": {
                            "ontology_signal_model": {
                                "member_vote_history": 1.0,
                            },
                        },
                        "feature_source_anchors_by_model": {
                            "ontology_signal_model": {
                                "member_vote_history": [
                                    {
                                        "source_type": "congress_vote",
                                        "source_id": "state-ca-2025-20",
                                        "url": (
                                            "https://leginfo.legislature.ca.gov/"
                                            "faces/billVotesClient.xhtml?bill_id=202520260AB1"
                                        ),
                                    }
                                ],
                            },
                        },
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = build_prediction_source_url_audit(report)

    assert result.missing_official_source_prediction_count == 1
    assert result.gaps[0].source_family_ids == ()
    assert result.gaps[0].sample_cases == ()
