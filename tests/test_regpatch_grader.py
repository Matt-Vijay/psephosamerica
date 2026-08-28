from __future__ import annotations

import json
from pathlib import Path

from src.regpatch.grader import PROJECTION_VERSION, SCORE_VERSION, score_episode

BEFORE = """\
<DIV5 N="73" TYPE="PART">
  <HEAD>PART 73—RADIO BROADCAST SERVICES</HEAD>
  <AUTH><PSPACE>47 U.S.C. 154 and 303.</PSPACE></AUTH>
  <DIV8 N="73.622" TYPE="SECTION">
    <HEAD>§ 73.622 Digital television table of allotments.</HEAD>
    <TABLE><TBODY>
      <TR><TD>Existing</TD><TD>20</TD></TR>
      <TR><TD>Wittenberg</TD><TD>31</TD></TR>
    </TBODY></TABLE>
  </DIV8>
  <DIV8 N="73.623" TYPE="SECTION">
    <HEAD>§ 73.623 Technical criteria.</HEAD>
    <XREF REFID="substantive-1">See § 73.622.</XREF>
    <P>(a) Preserve this cross-reference to § 73.622.</P>
  </DIV8>
</DIV5>
"""

TARGET = """\
<DIV5 N="73" TYPE="PART">
  <HEAD>PART 73—RADIO BROADCAST SERVICES</HEAD>
  <AUTH><PSPACE>47 U.S.C. 154 and 303.</PSPACE></AUTH>
  <DIV8 N="73.622" TYPE="SECTION">
    <HEAD>§ 73.622 Digital television table of allotments.</HEAD>
    <XREF ID="20240131" REFID="6" AMDINSN="2">Link to an amendment published at 89 FR 6024, Jan. 31, 2024.</XREF>
    <TABLE><TBODY>
      <TR><TD>Existing</TD><TD>20</TD></TR>
      <TR><TD>Shawano</TD><TD>31</TD></TR>
    </TBODY></TABLE>
  </DIV8>
  <DIV8 N="73.623" TYPE="SECTION">
    <HEAD>§ 73.623 Technical criteria.</HEAD>
    <XREF REFID="substantive-1">See § 73.622.</XREF>
    <P>(a) Preserve this cross-reference to § 73.622.</P>
  </DIV8>
</DIV5>
"""


def _write(path: Path, payload: str) -> Path:
    path.write_text(payload, encoding="utf-8")
    return path


def test_exact_target_is_100_and_copy_before_cannot_hide_in_unchanged_xml(
    tmp_path: Path,
) -> None:
    before = _write(tmp_path / "before.xml", BEFORE)
    target = _write(tmp_path / "target.xml", TARGET)
    manifest = {"episode_id": "seed", "changed_regions": ["47 CFR 73.622"]}

    candidate_without_editorial_xref = _write(
        tmp_path / "candidate.xml",
        TARGET.replace(
            '    <XREF ID="20240131" REFID="6" AMDINSN="2">Link to an amendment published at 89 FR 6024, Jan. 31, 2024.</XREF>\n',
            "",
        ),
    )
    exact = score_episode(
        manifest,
        before,
        target,
        candidate_without_editorial_xref,
        second_candidate_path=candidate_without_editorial_xref,
        provenance_ok=True,
    )
    assert exact.overall == 100.0
    assert set(exact.components.values()) == {1.0}
    assert exact.penalty_factor == 1.0
    assert exact.as_dict()["score_version"] == SCORE_VERSION
    assert exact.metrics["canonical_projection"] == {
        "version": PROJECTION_VERSION,
        "excluded_node_kinds": ["ecfr_editorial_amendment_xref", "ecfr_cita"],
        "table_normalization": "ordered_cells_spans_and_caption_text",
    }
    assert exact.counts["target_projected_editorial_witnesses"] == 1
    assert exact.counts["candidate_projected_editorial_witnesses"] == 0
    json.dumps(exact.as_dict(), sort_keys=True)

    missing_substantive_xref = _write(
        tmp_path / "missing-substantive.xml",
        candidate_without_editorial_xref.read_text(encoding="utf-8").replace(
            '    <XREF REFID="substantive-1">See § 73.622.</XREF>\n', ""
        ),
    )
    substantive_loss = score_episode(
        manifest,
        before,
        target,
        missing_substantive_xref,
        second_candidate_path=missing_substantive_xref,
        provenance_ok=True,
    )
    assert substantive_loss.overall < 100.0
    assert substantive_loss.penalties["dropped_unaffected"]["count"] == 1

    copied = score_episode(
        manifest,
        before,
        target,
        before,
        second_candidate_path=before,
        provenance_ok=True,
    )
    assert copied.components["changed_regions"] == 0.0
    assert copied.overall < 75.0
    assert copied.components["unaffected_preservation"] == 1.0


def test_malformed_nondeterministic_and_destructive_outputs_are_penalized(
    tmp_path: Path,
) -> None:
    before = _write(tmp_path / "before.xml", BEFORE)
    target = _write(tmp_path / "target.xml", TARGET)
    malformed = _write(tmp_path / "malformed.xml", "<!DOCTYPE x><x>&leak;</x>")
    destructive = _write(
        tmp_path / "destructive.xml",
        """\
<DIV5 N="73" TYPE="PART">
  <DIV8 N="73.622" TYPE="SECTION">
    <HEAD>§ 73.622 Fabricated.</HEAD>
    <TABLE><TR><TD>Shawano</TD><TD>31</TD></TR></TABLE>
  </DIV8>
  <DIV8 N="73.622" TYPE="SECTION">
    <HEAD>§ 73.622 Fabricated duplicate.</HEAD>
  </DIV8>
</DIV5>
""",
    )
    manifest = {"episode_id": "seed"}

    rejected = score_episode(
        manifest,
        before,
        target,
        malformed,
        second_candidate_path=malformed,
        provenance_ok=True,
    )
    assert rejected.overall == 0.0
    assert rejected.penalties["malformed_xml"]["count"] == 1

    unverified = score_episode(
        manifest,
        before,
        target,
        target,
        second_candidate_path=before,
        provenance_ok=False,
    )
    assert unverified.overall < 100.0
    assert unverified.penalties["missing_provenance"]["count"] == 1
    assert unverified.penalties["nondeterminism"]["count"] == 1

    damaged = score_episode(
        manifest,
        before,
        target,
        destructive,
        second_candidate_path=destructive,
        provenance_ok=True,
    )
    assert damaged.overall < 20.0
    assert damaged.penalties["fabricated_content"]["count"] > 0
    assert damaged.penalties["dropped_unaffected"]["count"] > 0
    assert damaged.penalties["duplicate_nodes"]["count"] > 0


def test_swapped_identity_anchored_siblings_lose_structure_credit(tmp_path: Path) -> None:
    ordered = _write(
        tmp_path / "ordered.xml",
        """<DIV5 N="73" TYPE="PART">
        <DIV8 N="73.1" TYPE="SECTION"><P>First.</P></DIV8>
        <DIV8 N="73.2" TYPE="SECTION"><P>Second.</P></DIV8>
        </DIV5>""",
    )
    swapped = _write(
        tmp_path / "swapped.xml",
        """<DIV5 N="73" TYPE="PART">
        <DIV8 N="73.2" TYPE="SECTION"><P>Second.</P></DIV8>
        <DIV8 N="73.1" TYPE="SECTION"><P>First.</P></DIV8>
        </DIV5>""",
    )
    manifest = {"episode_id": "sibling-order"}

    score = score_episode(
        manifest,
        ordered,
        ordered,
        swapped,
        second_candidate_path=swapped,
        provenance_ok=True,
    )

    assert score.overall < 100.0
    assert score.metrics["structure_integrity"]["sibling_order"] < 1.0
