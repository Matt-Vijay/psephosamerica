"""FEC and member-crosswalk subcommands."""

from __future__ import annotations

import argparse


def _add_load_fec_local(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "load-fec-local",
        help="Load local FEC bulk committee, candidate-linkage, and contribution files.",
    )
    p.add_argument(
        "--committee-master",
        required=True,
        metavar="PATH",
        help="Path to the extracted FEC Committee Master file, usually cm.txt.",
    )
    p.add_argument(
        "--candidate-committee-linkage",
        required=True,
        metavar="PATH",
        help="Path to the extracted FEC Candidate-Committee Linkage file, usually ccl.txt.",
    )
    p.add_argument(
        "--individual-contributions",
        required=True,
        metavar="PATH",
        help="Path to the extracted FEC individual contributions file, usually itcont.txt.",
    )
    p.add_argument(
        "--committee-source-url",
        default=None,
        metavar="URL",
        help="Optional official FEC source URL for the committee-master artifact.",
    )
    p.add_argument(
        "--linkage-source-url",
        default=None,
        metavar="URL",
        help="Optional official FEC source URL for the candidate-linkage artifact.",
    )
    p.add_argument(
        "--contribution-source-url",
        default=None,
        metavar="URL",
        help="Optional official FEC source URL for the contribution artifact.",
    )
    p.add_argument(
        "--contribution-chunk-size",
        type=int,
        default=50_000,
        metavar="N",
        help="Number of parsed contribution rows to load per DB batch.",
    )


def _add_materialize_fec_bulk_files(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "materialize-fec-bulk-files",
        help="Download/extract official FEC bulk ZIPs into local load-fec inputs.",
    )
    p.add_argument(
        "--cycle",
        type=int,
        required=True,
        metavar="YEAR",
        help="FEC election cycle year, e.g. 2024.",
    )
    p.add_argument(
        "--output-dir",
        default="data/fec",
        metavar="DIR",
        help="Directory for cm.txt, ccl.txt, and itcont.txt. Defaults to data/fec.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Plan official URLs and output paths without downloading.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite existing output files.",
    )
    p.add_argument(
        "--no-filter-individual-contributions",
        dest="filter_individual_contributions",
        action="store_false",
        default=True,
        help=(
            "Extract the full individual-contribution file instead of filtering "
            "to candidate committees present in ccl.txt."
        ),
    )
    p.add_argument(
        "--member-fec-crosswalk",
        default="data/crosswalks/member_fec.csv",
        metavar="PATH",
        help=(
            "Optional member_fec.csv used to narrow contribution filtering to "
            "committees linked to known congressional members."
        ),
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help="Network timeout per FEC ZIP download.",
    )
    p.add_argument(
        "--committee-url",
        default=None,
        metavar="URL",
        help="Override official Committee Master ZIP URL.",
    )
    p.add_argument(
        "--linkage-url",
        default=None,
        metavar="URL",
        help="Override official Candidate-Committee Linkage ZIP URL.",
    )
    p.add_argument(
        "--contribution-url",
        default=None,
        metavar="URL",
        help="Override official Individual Contributions ZIP URL.",
    )
    p.add_argument(
        "--summary-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the materialization summary.",
    )


def _add_load_member_fec_crosswalk_local(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "load-member-fec-crosswalk-local",
        help="Load a local bioguide_id to FEC candidate ID crosswalk CSV.",
    )
    p.add_argument(
        "--crosswalk",
        required=True,
        metavar="PATH",
        help="CSV path with bioguide_id and fec_candidate_id columns.",
    )
    p.add_argument(
        "--member-terms",
        default=None,
        metavar="PATH",
        help="Optional companion CSV with historical member terms keyed by bioguide_id.",
    )
    p.add_argument(
        "--source-url",
        default=None,
        metavar="URL",
        help="Optional source URL for the crosswalk artifact.",
    )


def _add_verify_fec_inputs(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "verify-fec-inputs",
        help="Verify local FEC bulk files and optional member-FEC crosswalk before load.",
    )
    p.add_argument(
        "--committee-master",
        required=True,
        metavar="PATH",
        help="Path to the extracted FEC Committee Master file, usually cm.txt.",
    )
    p.add_argument(
        "--candidate-committee-linkage",
        required=True,
        metavar="PATH",
        help="Path to the extracted FEC Candidate-Committee Linkage file, usually ccl.txt.",
    )
    p.add_argument(
        "--individual-contributions",
        required=True,
        metavar="PATH",
        help="Path to the extracted FEC individual contributions file, usually itcont.txt.",
    )
    p.add_argument(
        "--member-fec-crosswalk",
        default=None,
        metavar="PATH",
        help="Optional bioguide_id,fec_candidate_id crosswalk CSV to verify too.",
    )
    p.add_argument(
        "--require-member-fec-crosswalk",
        action="store_true",
        default=False,
        help="Fail unless --member-fec-crosswalk exists and has valid rows.",
    )
    p.add_argument(
        "--min-committee-rows",
        type=int,
        default=None,
        metavar="N",
        help="Minimum non-empty Committee Master rows required.",
    )
    p.add_argument(
        "--min-linkage-rows",
        type=int,
        default=None,
        metavar="N",
        help="Minimum non-empty Candidate-Committee Linkage rows required.",
    )
    p.add_argument(
        "--min-contribution-rows",
        type=int,
        default=None,
        metavar="N",
        help="Minimum non-empty individual-contribution rows required.",
    )
    p.add_argument(
        "--min-member-fec-rows",
        type=int,
        default=None,
        metavar="N",
        help="Minimum member-FEC crosswalk rows required.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the FEC input verification payload.",
    )


def _add_materialize_member_fec_crosswalk(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    p = sub.add_parser(
        "materialize-member-fec-crosswalk",
        help="Build data/crosswalks/member_fec.csv from public legislator IDs.",
    )
    p.add_argument(
        "--output",
        default="data/crosswalks/member_fec.csv",
        metavar="PATH",
        help="Output CSV path. Defaults to data/crosswalks/member_fec.csv.",
    )
    p.add_argument(
        "--terms-output",
        default=None,
        metavar="PATH",
        help="Optional companion member terms CSV path materialized from the same source.",
    )
    p.add_argument(
        "--source-file",
        default=None,
        metavar="PATH",
        help="Optional local legislators-current.yaml source file.",
    )
    p.add_argument(
        "--source-url",
        default=(
            "https://raw.githubusercontent.com/unitedstates/congress-legislators/"
            "main/legislators-current.yaml"
        ),
        metavar="URL",
        help="HTTPS source URL used when --source-file is absent and recorded in summary.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Report source/output paths without downloading or writing.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite an existing output CSV.",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help="Network timeout when downloading the source YAML.",
    )
    p.add_argument(
        "--summary-output",
        default=None,
        metavar="PATH",
        help="Optional JSON path for the materialization summary.",
    )
