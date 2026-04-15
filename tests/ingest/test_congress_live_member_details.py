"""Tests for live_member_details — no network; detail fetchers and parse helpers mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.ingest.congress.models import MemberRecord

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MEMBER_A = MemberRecord(
    bioguide_id="A000001",
    first_name="Ada",
    last_name="Lovelace",
    full_name="Ada Lovelace",
    chamber="house",
    state="CA",
    is_current=True,
)

_MEMBER_B = MemberRecord(
    bioguide_id="B000002",
    first_name="Bob",
    last_name="Smith",
    full_name="Bob Smith",
    chamber="senate",
    state="TX",
    is_current=True,
)

# Simulate the full API envelope returned by get_member_detail_payload.
_ENVELOPE_A: dict = {"member": {"bioguideId": "A000001", "terms": {"item": []}}}
_ENVELOPE_B: dict = {"member": {"bioguideId": "B000002", "terms": {"item": []}}}

_INNER_A = _ENVELOPE_A["member"]
_INNER_B = _ENVELOPE_B["member"]


def _make_client(*envelopes: dict) -> MagicMock:
    client = MagicMock()
    client.get_member_detail_payload.side_effect = list(envelopes)
    return client


# ---------------------------------------------------------------------------
# Parametrized tests for fetch_member_term_specs & fetch_committee_membership_specs
#
# Both functions share the same fetch-detail-then-parse structure. The only
# difference is the function under test and the parse helper it delegates to.
# ---------------------------------------------------------------------------

_MOD = "src.ingest.congress.live_member_details"

_DETAIL_FETCH_CASES = [
    pytest.param(
        "fetch_member_term_specs",
        "member_term_specs_from_detail",
        id="term_specs",
    ),
    pytest.param(
        "fetch_committee_membership_specs",
        "committee_membership_specs_from_detail",
        id="membership_specs",
    ),
]


def _import_fn(fn_name: str):
    import src.ingest.congress.live_member_details as mod

    return getattr(mod, fn_name)


class TestDetailFetchFunctions:
    """Covers fetch_member_term_specs and fetch_committee_membership_specs.

    Both follow the identical pattern: iterate members, call
    get_member_detail_payload, unwrap the envelope, pass to a parser,
    flatten the results.  The parametrized ``fn_name`` / ``parser_name``
    pair selects which concrete function + parser to exercise.
    """

    @pytest.mark.parametrize("fn_name,parser_name", _DETAIL_FETCH_CASES)
    def test_returns_empty_for_no_members(self, fn_name, parser_name) -> None:
        client = _make_client()
        result = _import_fn(fn_name)(client, [])
        assert result == []
        client.get_member_detail_payload.assert_not_called()

    @pytest.mark.parametrize("fn_name,parser_name", _DETAIL_FETCH_CASES)
    def test_fetches_detail_for_each_member(self, fn_name, parser_name) -> None:
        client = _make_client(_ENVELOPE_A, _ENVELOPE_B)
        fake_spec = MagicMock()
        with patch(f"{_MOD}.{parser_name}", return_value=[fake_spec]):
            result = _import_fn(fn_name)(client, [_MEMBER_A, _MEMBER_B])
        assert client.get_member_detail_payload.call_count == 2
        assert len(result) == 2

    @pytest.mark.parametrize("fn_name,parser_name", _DETAIL_FETCH_CASES)
    def test_passes_unwrapped_inner_and_member_to_parser(self, fn_name, parser_name) -> None:
        member = _MEMBER_A if parser_name == "member_term_specs_from_detail" else _MEMBER_B
        envelope = _ENVELOPE_A if member is _MEMBER_A else _ENVELOPE_B
        inner = envelope["member"]

        client = _make_client(envelope)
        with patch(f"{_MOD}.{parser_name}", return_value=[]) as mock_parse:
            _import_fn(fn_name)(client, [member])
        mock_parse.assert_called_once_with(inner, member)

    @pytest.mark.parametrize("fn_name,parser_name", _DETAIL_FETCH_CASES)
    def test_calls_correct_member_detail_url(self, fn_name, parser_name) -> None:
        member = _MEMBER_A if parser_name == "member_term_specs_from_detail" else _MEMBER_B
        envelope = _ENVELOPE_A if member is _MEMBER_A else _ENVELOPE_B

        client = _make_client(envelope)
        with patch(f"{_MOD}.{parser_name}", return_value=[]):
            _import_fn(fn_name)(client, [member])
        client.get_member_detail_payload.assert_called_once_with(member.bioguide_id)

    @pytest.mark.parametrize("fn_name,parser_name", _DETAIL_FETCH_CASES)
    def test_preserves_input_member_order(self, fn_name, parser_name) -> None:
        spec_a = MagicMock(name="spec_a")
        spec_b = MagicMock(name="spec_b")
        client = _make_client(_ENVELOPE_A, _ENVELOPE_B)

        def _parse(inner: dict, member: MemberRecord) -> list:
            return [spec_a] if member.bioguide_id == "A000001" else [spec_b]

        with patch(f"{_MOD}.{parser_name}", side_effect=_parse):
            result = _import_fn(fn_name)(client, [_MEMBER_A, _MEMBER_B])
        assert result == [spec_a, spec_b]

    @pytest.mark.parametrize("fn_name,parser_name", _DETAIL_FETCH_CASES)
    def test_flattens_multiple_specs_per_member(self, fn_name, parser_name) -> None:
        spec_a1, spec_a2, spec_b1 = MagicMock(), MagicMock(), MagicMock()
        client = _make_client(_ENVELOPE_A, _ENVELOPE_B)

        def _parse(inner: dict, member: MemberRecord) -> list:
            return [spec_a1, spec_a2] if member.bioguide_id == "A000001" else [spec_b1]

        with patch(f"{_MOD}.{parser_name}", side_effect=_parse):
            result = _import_fn(fn_name)(client, [_MEMBER_A, _MEMBER_B])
        assert result == [spec_a1, spec_a2, spec_b1]

    @pytest.mark.parametrize("fn_name,parser_name", _DETAIL_FETCH_CASES)
    def test_member_with_no_results_contributes_nothing(self, fn_name, parser_name) -> None:
        client = _make_client(_ENVELOPE_A)
        with patch(f"{_MOD}.{parser_name}", return_value=[]):
            result = _import_fn(fn_name)(client, [_MEMBER_A])
        assert result == []

    @pytest.mark.parametrize("fn_name,parser_name", _DETAIL_FETCH_CASES)
    def test_api_error_propagates(self, fn_name, parser_name) -> None:
        client = MagicMock()
        client.get_member_detail_payload.side_effect = RuntimeError("network failure")
        with pytest.raises(RuntimeError, match="network failure"):
            with patch(f"{_MOD}.{parser_name}", return_value=[]):
                _import_fn(fn_name)(client, [_MEMBER_A])


# ---------------------------------------------------------------------------
# Shared fetch mechanism: both functions use member_detail_url
# ---------------------------------------------------------------------------


class TestDetailFetchMechanism:
    def test_unwraps_member_envelope_before_parsing(self) -> None:
        """The inner member dict (not the full envelope) reaches the parse helpers."""
        from src.ingest.congress.live_member_details import fetch_member_term_specs

        inner = {"bioguideId": "A000001", "terms": {"item": []}}
        envelope = {"member": inner, "request": {}}

        client = _make_client(envelope)

        with patch(
            "src.ingest.congress.live_member_details.member_term_specs_from_detail",
            return_value=[],
        ) as mock_parse:
            fetch_member_term_specs(client, [_MEMBER_A])

        mock_parse.assert_called_once_with(inner, _MEMBER_A)

    def test_each_function_uses_member_detail_payload(self) -> None:
        from src.ingest.congress.live_member_details import (
            fetch_committee_membership_specs,
            fetch_member_term_specs,
        )

        for fetch_fn, parse_target in (
            (fetch_member_term_specs, "member_term_specs_from_detail"),
            (fetch_committee_membership_specs, "committee_membership_specs_from_detail"),
        ):
            client = _make_client(_ENVELOPE_A)
            with patch(
                f"src.ingest.congress.live_member_details.{parse_target}",
                return_value=[],
            ):
                fetch_fn(client, [_MEMBER_A])

            client.get_member_detail_payload.assert_called_once_with("A000001")

    def test_both_parsers_receive_same_inner_payload(self) -> None:
        from src.ingest.congress.live_member_details import fetch_member_detail_specs

        inner = {"bioguideId": "A000001", "terms": {"item": []}, "committees": {"item": []}}
        envelope = {"member": inner}

        client = MagicMock()
        client.get_member_detail_payload.return_value = envelope

        received_term_inner: list = []
        received_committee_inner: list = []

        def _term_parse(i: dict, m: MemberRecord) -> list:
            received_term_inner.append(i)
            return []

        def _committee_parse(i: dict, m: MemberRecord) -> list:
            received_committee_inner.append(i)
            return []

        with (
            patch(
                "src.ingest.congress.live_member_details.member_term_specs_from_detail",
                side_effect=_term_parse,
            ),
            patch(
                "src.ingest.congress.live_member_details.committee_membership_specs_from_detail",
                side_effect=_committee_parse,
            ),
        ):
            fetch_member_detail_specs(client, [_MEMBER_A])

        assert received_term_inner[0] is inner
        assert received_committee_inner[0] is inner
        client.get_member_detail_payload.assert_called_once_with("A000001")


# ---------------------------------------------------------------------------
# Realistic payload shapes
# ---------------------------------------------------------------------------

# Full Congress.gov-style inner member object (already unwrapped from "member" key).
_REALISTIC_INNER_WARREN: dict = {
    "bioguideId": "W000817",
    "directOrderName": "Warren, Elizabeth",
    "firstName": "Elizabeth",
    "lastName": "Warren",
    "party": "Democrat",
    "stateCode": "MA",
    "officialWebsiteUrl": "https://www.warren.senate.gov",
    "terms": {
        "item": [
            {
                "chamber": "Senate",
                "congress": 119,
                "endYear": None,
                "memberType": "Senator",
                "startYear": "2025",
                "stateCode": "MA",
            }
        ]
    },
    "committees": {
        "item": [
            {
                "committee": {
                    "name": "Committee on Banking, Housing, and Urban Affairs",
                    "systemCode": "ssbk00",
                    "url": "https://api.congress.gov/v3/committee/senate/ssbk00",
                },
                "congress": 119,
                "endDate": None,
                "isCurrent": True,
                "role": "Member",
                "startDate": "2025-01-15",
            }
        ]
    },
}

# Envelope shape (as would be returned by a mock that has NOT yet been unwrapped).
_REALISTIC_ENVELOPE_WARREN: dict = {
    "member": _REALISTIC_INNER_WARREN,
    "request": {
        "bioguideId": "W000817",
        "contentType": "application/json",
        "format": "json",
    },
}

_MEMBER_WARREN = MemberRecord(
    bioguide_id="W000817",
    first_name="Elizabeth",
    last_name="Warren",
    full_name="Elizabeth Warren",
    chamber="senate",
    state="MA",
    is_current=True,
)


class TestRealisticPayloadRouting:
    """Verify that fetch functions route realistic Congress.gov payloads through
    the parse helpers without loss, regardless of whether the envelope has already
    been unwrapped by the client layer."""

    def test_envelope_payload_routes_inner_to_parsers(self) -> None:
        """When the mock returns the full API envelope, inner member dict reaches parsers."""
        from src.ingest.congress.live_member_details import fetch_member_term_specs

        client = _make_client(_REALISTIC_ENVELOPE_WARREN)
        received: list[dict] = []

        def _capture(inner: dict, member: MemberRecord) -> list:
            received.append(inner)
            return []

        with patch(
            "src.ingest.congress.live_member_details.member_term_specs_from_detail",
            side_effect=_capture,
        ):
            fetch_member_term_specs(client, [_MEMBER_WARREN])

        assert received[0] is _REALISTIC_INNER_WARREN

    def test_bare_inner_payload_also_routes_correctly(self) -> None:
        """When the client already unwraps (production path), the inner dict
        is still passed correctly — the code is safe regardless of wrapping."""
        from src.ingest.congress.live_member_details import fetch_member_term_specs

        # Simulate what the real CongressAPIClient returns: already-unwrapped inner.
        client = _make_client(_REALISTIC_INNER_WARREN)
        received: list[dict] = []

        def _capture(inner: dict, member: MemberRecord) -> list:
            received.append(inner)
            return []

        with patch(
            "src.ingest.congress.live_member_details.member_term_specs_from_detail",
            side_effect=_capture,
        ):
            fetch_member_term_specs(client, [_MEMBER_WARREN])

        # detail.get("member", detail) falls back to detail itself when no "member" key.
        assert received[0] is _REALISTIC_INNER_WARREN

    def test_realistic_envelope_both_parsers_see_same_inner(self) -> None:
        from src.ingest.congress.live_member_details import fetch_member_detail_specs

        client = MagicMock()
        client.get_member_detail_payload.return_value = _REALISTIC_ENVELOPE_WARREN

        seen_term: list[dict] = []
        seen_committee: list[dict] = []

        with (
            patch(
                "src.ingest.congress.live_member_details.member_term_specs_from_detail",
                side_effect=lambda i, m: seen_term.append(i) or [],
            ),
            patch(
                "src.ingest.congress.live_member_details.committee_membership_specs_from_detail",
                side_effect=lambda i, m: seen_committee.append(i) or [],
            ),
        ):
            fetch_member_detail_specs(client, [_MEMBER_WARREN])

        assert seen_term[0] is _REALISTIC_INNER_WARREN
        assert seen_committee[0] is _REALISTIC_INNER_WARREN
