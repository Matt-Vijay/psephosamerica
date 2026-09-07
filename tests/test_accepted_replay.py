"""Offline replay trust boundaries, using only generated temporary fixtures."""

import hashlib
import importlib.util
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from conftest import retain

from psephos.store import Provision, Reference

REPO = Path(__file__).resolve().parents[1]
REPLAY = REPO / "replay"


def checkout_module(name, filename):
    # Fixed repository-owned code, never an executable path selected by a recipe.
    spec = importlib.util.spec_from_file_location(name, REPLAY / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = checkout_module("accepted_replay_runner_test", "replay.py")
fl_nj = checkout_module("accepted_replay_fl_nj_test", "fl_nj.py")
mississippi = checkout_module("accepted_replay_ms_test", "mississippi.py")
IDENTITY = {"family": "fixture", "recipe_sha256": "fixture", "profile": "accepted-text-2"}


def child_script(source):
    return subprocess.run(
        [sys.executable, "-B", "-c", source],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_historical_core_materializes_hash_pinned_text2_in_clean_process():
    result = child_script(
        """
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'replay'))
import replay
assert 'psephos' not in sys.modules
original_run = replay.subprocess.run
def checked_git(*args, **kwargs):
    assert kwargs['env']['GIT_NO_LAZY_FETCH'] == '1'
    assert kwargs['env']['GIT_ALLOW_PROTOCOL'] == ''
    return original_run(*args, **kwargs)
replay.subprocess.run = checked_git
with replay.historical_runtime():
    import psephos
    import psephos.parse
    import psephos.store
    from lxml import html
    root = Path(psephos.__file__).parent
    assert root != Path.cwd() / 'src/psephos'
    for name, expected in replay.CORE_HASHES.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
    assert 'parser += "/text-2"' in (root / 'store.py').read_text()
    assert psephos.parse.readable(html.fromstring('<ol><li>A</li><li>B</li></ol>')) == 'A\\nB'
assert not root.exists()
print('pinned text-2 verified')
"""
    )
    assert result.stdout.strip() == "pinned text-2 verified"


def test_output_ownership_rejects_existing_store_unowned_and_report_symlink(tmp_path):
    objects = tmp_path / "retained-objects"
    objects.mkdir()
    canonical = tmp_path / "fixture-canonical"
    canonical.mkdir()
    database = canonical / "legal.sqlite3"
    database.write_bytes(b"untouched fixture database")
    for output in (canonical, canonical / "nested-replay"):
        with pytest.raises(ValueError):
            with runner.owned_output(output, objects, IDENTITY):
                pytest.fail("An existing Store boundary was admitted")
    unowned = tmp_path / "unowned"
    unowned.mkdir()
    (unowned / "user-file").write_text("preserve")
    with pytest.raises(ValueError, match="unowned"):
        with runner.owned_output(unowned, objects, IDENTITY):
            pytest.fail("A nonempty unowned output was admitted")
    output = tmp_path / "owned"
    with runner.owned_output(output, objects, IDENTITY):
        pass
    (output / "replay-result.json").symlink_to(database)
    with pytest.raises(ValueError, match="Symlink"):
        with runner.owned_output(output, objects, IDENTITY):
            pytest.fail("A report symlink was admitted")
    assert database.read_bytes() == b"untouched fixture database"
    assert not (canonical / "nested-replay").exists()
    assert (unowned / "user-file").read_text() == "preserve"


def test_install_inputs_rejects_symlinked_object_ancestor_before_mkdir(tmp_path):
    raw = b"generated immutable artifact fixture"
    sha = hashlib.sha256(raw).hexdigest()
    retained = tmp_path / "retained"
    source = retained / sha[:2] / sha
    source.parent.mkdir(parents=True)
    source.write_bytes(raw)
    output = tmp_path / "output"
    output.mkdir()
    outside = tmp_path / "must-stay-empty"
    outside.mkdir()
    (output / "objects").symlink_to(outside, target_is_directory=True)
    store = SimpleNamespace(
        root=output,
        object_path=lambda value: output / "objects" / value[:2] / value,
    )
    recipe = {
        "artifacts": [{"sha256": sha, "bytes": len(raw)}],
        "acquisitions": [],
        "jurisdictions": [],
        "collections": [],
    }
    with pytest.raises(ValueError, match="Symlink"):
        runner.install_inputs(store, recipe, retained)
    assert list(outside.iterdir()) == []
    assert source.read_bytes() == raw


def test_reference_fingerprint_matches_store_first_wins_and_all_source_fields(store):
    receipt = retain(store, b"generated source fixture")
    first = Reference("https://example.test/next", "publisher_link", "Next", "first evidence")
    duplicate = replace(first, evidence="later evidence must not replace first")
    other = replace(first, relation="publisher_citation_identifier", evidence="another relation")
    unit = Provision(
        key="fixture:1",
        citation="Fixture § 1",
        heading="Fixture heading",
        text="The complete fixture wording shall remain unchanged.",
        markup="<section>fixture</section>",
        url=receipt.url,
        parent_key="fixture",
        metadata={"source_member": "fixture.txt", "clock": None},
        references=(first, duplicate, other),
    )
    version, _, _ = store.ingest(
        collection="test",
        document="fixture",
        title="Fixture",
        url=receipt.url,
        acquisition=receipt.id,
        snapshot_basis="Unknown fixture date",
        parser="fixture",
        provisions=[unit],
    )
    expected = runner.unit_fingerprint([unit])
    assert expected[1:] == (1, 2)
    assert runner.stored_fingerprint(store.db, version) == expected
    assert runner.unit_fingerprint([replace(unit, references=(first, other))]) == expected
    assert (
        runner.unit_fingerprint([replace(unit, references=(duplicate, first, other))]) != expected
    )
    for field in runner.UNIT_FIELDS:
        changed = replace(unit, **{field: "changed fixture field"})
        assert runner.unit_fingerprint([changed]) != expected, field
    assert runner.unit_fingerprint([replace(unit, metadata={"clock": "invented"})]) != expected


def test_offline_audit_rejects_socket_creation_in_separate_process():
    result = child_script(
        """
import socket
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'replay'))
import replay
sys.addaudithook(replay.offline)
try:
    socket.socket()
except PermissionError as error:
    assert 'Offline replay forbids network access' in str(error)
else:
    raise AssertionError('Socket creation was permitted')
print('socket blocked before any connection')
"""
    )
    assert result.stdout.strip() == "socket blocked before any connection"


def test_changed_recipe_is_rejected_before_output_creation(tmp_path, monkeypatch):
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    (recipes / "fl.json").write_text('{"family":"fl","expected_units":"edited"}')
    monkeypatch.setattr(runner, "HERE", tmp_path)
    output = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="Sealed recipe changed"):
        runner.run("fl", tmp_path / "unused", output, 1)
    assert not output.exists()


def test_zip_member_rejects_duplicate_size_and_hash_mismatches(tmp_path):
    raw = b"generated member bytes"
    sha = hashlib.sha256(raw).hexdigest()
    path = tmp_path / "fixture.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("SOURCE.TXT", raw)
    assert runner.zip_member(path, "SOURCE.TXT", len(raw), sha) == raw
    with pytest.raises(ValueError, match="size"):
        runner.zip_member(path, "SOURCE.TXT", len(raw) + 1, sha)
    with pytest.raises(ValueError, match="Corrupt member"):
        runner.zip_member(path, "SOURCE.TXT", len(raw), "0" * 64)
    duplicate = tmp_path / "duplicate.zip"
    with ZipFile(duplicate, "w") as archive:
        archive.writestr("SOURCE.TXT", raw)
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("SOURCE.TXT", raw)
    with pytest.raises(ValueError, match="Missing/duplicate"):
        runner.zip_member(duplicate, "SOURCE.TXT", len(raw), sha)


def test_nj_acceptance_overlay_requires_exact_archive_hash_range_and_text():
    text = "THE SECTION PREVIOUSLY ALLOCATED AS 54A:2-1b HAS BEEN REALLOCATED TO C.54A:3-9\r\n\r\n"
    prepared = fl_nj.PreparedNJ(
        text="",
        volumes=(),
        headnotes=(),
        blank_headnotes=0,
        txt_sha256=fl_nj.NJ_STATUTES_TXT_SHA256,
        rtf_sha256=fl_nj.NJ_STATUTES_RTF_SHA256,
        artifact_sha=fl_nj.NJ_STATUTES_ARCHIVE_SHA256,
    )
    unit = Provision(
        key="nj-statutes:THE",
        citation="N.J. Stat. § THE",
        heading=text.strip(),
        text=text,
        markup="",
        url=fl_nj.NJ_BASE + "STATUTES-TEXT.zip",
        parent_key="nj-statutes:54A",
        unit_kind="disposition_note",
        metadata={
            "source_member": "STATUTES.TXT",
            "source_rtf_member": "STATUTES.RTF",
            "source_start_byte": 79052965,
            "source_end_byte": 79053047,
        },
    )
    apply = fl_nj._accepted_nj_classification
    assert apply(unit, prepared, "nj-statutes:54A") == replace(unit, unit_kind="source_block")
    cases = [
        (unit, replace(prepared, artifact_sha=None), "nj-statutes:54A"),
        (unit, replace(prepared, artifact_sha="0" * 64), "nj-statutes:54A"),
        (unit, replace(prepared, txt_sha256="0" * 64), "nj-statutes:54A"),
        (unit, replace(prepared, rtf_sha256="0" * 64), "nj-statutes:54A"),
        (unit, prepared, "nj-statutes:54"),
        (replace(unit, text=text + " "), prepared, "nj-statutes:54A"),
        (replace(unit, unit_kind="section"), prepared, "nj-statutes:54A"),
        (
            replace(unit, metadata={**unit.metadata, "source_end_byte": 79053048}),
            prepared,
            "nj-statutes:54A",
        ),
    ]
    for candidate, context, document in cases:
        with pytest.raises(ValueError, match="overlay guard failed"):
            apply(candidate, context, document)
    unrelated = replace(unit, key="nj-statutes:other")
    assert apply(unrelated, prepared, "nj-statutes:54A") is unrelated
    with pytest.raises(ValueError, match="exact accepted"):
        fl_nj.prepare_nj(b"unaccepted TXT", b"unaccepted RTF")


def test_ms_profile_cannot_omit_unreviewed_or_nondefault_metadata(tmp_path, monkeypatch):
    # Mock the PDF decoder/native extractor, not profile-validation behavior.
    raw = b"%PDF-generated-offline-fixture"
    path = tmp_path / "fixture.pdf"
    path.write_bytes(raw)
    page_text = "A person shall preserve the complete wording of this fixture rule. " * 12
    reader = SimpleNamespace(pages=[{}], named_destinations={}, page_labels=["1"], metadata={})
    monkeypatch.setattr(mississippi, "PdfReader", lambda stream: reader)
    monkeypatch.setattr(mississippi.shutil, "which", lambda name: "/fixture/pdftotext")
    monkeypatch.setattr(
        mississippi.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=(page_text + "\f").encode()),
    )
    profile = {
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "accepted_reader_identity": None,
        "omitted_metadata_keys": ["text_gaps"],
    }

    def project(snapshot, review=None):
        return mississippi.project_pdf(
            path,
            "fixture",
            "Fixture Rules",
            "https://example.test/fixture.pdf",
            1,
            review or {},
            snapshot_profile=snapshot,
        )

    assert "text_gaps" not in project(profile).metadata
    with pytest.raises(ValueError, match="Cannot omit non-default or unreviewed"):
        project(profile, {"text_gaps": ["An actual source gap must survive"]})
    with pytest.raises(ValueError, match="Cannot omit non-default or unreviewed"):
        project({**profile, "omitted_metadata_keys": ["clock_note"]})
    with pytest.raises(ValueError, match="list of field names"):
        project({**profile, "omitted_metadata_keys": "text_gaps"})
    with pytest.raises(ValueError, match="does not match artifact"):
        project({**profile, "artifact_sha256": "0" * 64})
    with pytest.raises(ValueError, match="positive integer or null"):
        project({**profile, "accepted_reader_identity": True})
