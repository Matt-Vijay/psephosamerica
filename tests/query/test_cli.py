"""Tests for the ask-anything CLI (`python -m src.query`)."""

from __future__ import annotations

import json

import pytest

from src.query import __main__ as cli
from src.query.graph_store import Edge, GraphStore, Node, Provenance


def _prov(url: str) -> Provenance:
    return Provenance(source_url=url, content_sha256="h", known_at="2026-06-01T00:00:00Z")


def _store() -> GraphStore:
    store = GraphStore()
    store.add_node(
        Node(
            "ce-a",
            "person",
            "Alice Adams",
            (),
            "2026-06-01T00:00:00Z",
            (_prov("https://p/a"),),
            "us-congress",
        )
    )
    store.add_node(
        Node(
            "cb-1",
            "bill",
            "Budget Act",
            (),
            "2026-06-01T00:00:00Z",
            (_prov("https://b/1"),),
            "us-congress",
        )
    )
    store.add_edge(Edge("vote", "ce-a", "cb-1", {"choice": "yea"}, _prov("https://v/1")))
    return store


def test_ask_prints_cited_grounded_answer(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli, "build_store", lambda **kw: _store())
    rc = cli.main(["ask", "Who voted on the budget?"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "grounded stub" in out
    assert "Mode:" in out


def test_ask_json_mode(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli, "build_store", lambda **kw: _store())
    rc = cli.main(["ask", "budget", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["question"] == "budget"
    assert "citations" in payload


def test_lens_accountability(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "build_store", lambda **kw: _store())
    rc = cli.main(["lens", "accountability", "--limit", "5"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Alice Adams" in out


def test_lens_jurisdictions_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "build_store", lambda **kw: _store())
    rc = cli.main(["lens", "jurisdictions", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert any(r["jurisdiction"] == "us-congress" for r in payload)


def test_said_vs_voted_cli(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "build_store", lambda **kw: _store())
    rc = cli.main(["said-vs-voted", "ce-a"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Said vs voted" in out
    assert "Alice Adams" in out


def test_said_vs_voted_unknown_returns_nonzero(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "build_store", lambda **kw: _store())
    rc = cli.main(["said-vs-voted", "ce-missing"])
    assert rc == 1


def test_no_command_errors() -> None:
    with pytest.raises(SystemExit):
        cli.main([])
