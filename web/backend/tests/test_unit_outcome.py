"""Unit tests for the Predictive Accuracy Ledger.

The ledger writes a single ``<sim_dir>/outcome.json`` file per public
simulation, and the gallery helper + ``?verified=1`` filter both read
from it. These tests exercise the helpers directly (no Flask, no DB)
so they run in the bare unit environment.

We cover:

  1. ``_read_outcome_file`` parses well-formed records and returns
     ``None`` for missing / malformed / unknown-label artifacts.
  2. ``_read_outcome_file`` truncates oversized summaries and rejects
     non-http URL schemes — defense-in-depth against a corrupt file
     leaking ``javascript:`` onto the gallery.
  3. ``_build_gallery_card_payload`` surfaces the ``outcome`` field on
     the card so the explore grid can render the ◎ Verified pill.
  4. ``_build_gallery_card_payload`` keeps degrading gracefully — a
     missing or corrupt outcome.json must not raise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


class _FakeStatus:
    value = "completed"


def _make_state(simulation_id: str = "sim_outcome_test"):
    """Lightweight stand-in for SimulationState — only the attributes the
    gallery helper reads."""
    class _State:
        pass
    s = _State()
    s.simulation_id = simulation_id
    s.is_public = True
    s.profiles_count = 200
    s.created_at = "2026-04-26T12:00:00"
    s.parent_simulation_id = None
    s.status = _FakeStatus()
    return s


@pytest.fixture(autouse=True)
def _no_runner_state():
    """Bypass SimulationRunner.get_run_state — same trick as the gallery
    test file, so the helper falls back to disk-only data."""
    from app.api import simulation as sim_mod
    with patch.object(sim_mod.SimulationRunner, "get_run_state", return_value=None):
        yield


# ──────────────────────────────────────────────────────────────────────────
# _read_outcome_file
# ──────────────────────────────────────────────────────────────────────────


def test_read_outcome_returns_none_when_missing(tmp_path: Path):
    """No outcome.json on disk ⇒ no outcome on the card. The helper must
    return ``None`` rather than raise — corrupt-or-missing states are the
    common case for any sim that hasn't been annotated."""
    from app.api.simulation import _read_outcome_file

    assert _read_outcome_file(str(tmp_path)) is None


def test_read_outcome_parses_valid_record(tmp_path: Path):
    from app.api.simulation import _read_outcome_file

    (tmp_path / "outcome.json").write_text(json.dumps({
        "label": "correct",
        "outcome_url": "https://example.com/aave-incident",
        "outcome_summary": "Aave's GHO peg broke as the agents predicted.",
        "submitted_at": "2026-04-27T10:00:00+00:00",
    }))

    out = _read_outcome_file(str(tmp_path))
    assert out is not None
    assert out["label"] == "correct"
    assert out["outcome_url"] == "https://example.com/aave-incident"
    assert "Aave" in out["outcome_summary"]
    assert out["submitted_at"].startswith("2026-04-27")


def test_read_outcome_drops_unknown_label(tmp_path: Path):
    """An artifact with an out-of-vocabulary label must not render — the
    gallery contract is correct/incorrect/partial, period."""
    from app.api.simulation import _read_outcome_file

    (tmp_path / "outcome.json").write_text(json.dumps({
        "label": "MAYBE",
        "outcome_summary": "ambiguous",
    }))

    assert _read_outcome_file(str(tmp_path)) is None


def test_read_outcome_swallows_corrupt_json(tmp_path: Path):
    """A corrupt outcome.json shouldn't take down the gallery. Same
    posture as the rest of the gallery helper."""
    from app.api.simulation import _read_outcome_file

    (tmp_path / "outcome.json").write_text("{this is not json")

    assert _read_outcome_file(str(tmp_path)) is None


def test_read_outcome_truncates_oversized_summary(tmp_path: Path):
    """A 280-char summary cap protects the gallery layout. The helper
    truncates with an ellipsis when the file on disk is longer than that
    — defensive, since the writer also enforces the cap."""
    from app.api.simulation import _read_outcome_file

    long_summary = "X" * 320
    (tmp_path / "outcome.json").write_text(json.dumps({
        "label": "partial",
        "outcome_summary": long_summary,
    }))

    out = _read_outcome_file(str(tmp_path))
    assert out is not None
    assert len(out["outcome_summary"]) <= 280
    assert out["outcome_summary"].endswith("…")


def test_read_outcome_drops_non_http_url(tmp_path: Path):
    """Only http(s) URLs make it onto the card — we never want a stored
    ``javascript:`` or ``file://`` value to round-trip into a gallery
    pill that links to it."""
    from app.api.simulation import _read_outcome_file

    (tmp_path / "outcome.json").write_text(json.dumps({
        "label": "correct",
        "outcome_url": "javascript:alert(1)",
        "outcome_summary": "ok",
    }))

    out = _read_outcome_file(str(tmp_path))
    assert out is not None
    assert out["outcome_url"] == ""
    assert out["label"] == "correct"


# ──────────────────────────────────────────────────────────────────────────
# Gallery card integration
# ──────────────────────────────────────────────────────────────────────────


def test_gallery_card_includes_outcome_when_present(tmp_path: Path):
    """Once an outcome is recorded, the gallery card surfaces it under an
    ``outcome`` key so the /explore grid can render the verified pill."""
    from app.api.simulation import _build_gallery_card_payload

    (tmp_path / "simulation_config.json").write_text(json.dumps({
        "simulation_requirement": "Will Aave's GHO depeg under stress?",
        "time_config": {"minutes_per_round": 60, "total_simulation_hours": 12},
    }))
    (tmp_path / "outcome.json").write_text(json.dumps({
        "label": "correct",
        "outcome_url": "https://example.com/aave-incident",
        "outcome_summary": "Depegged within 4 hours of the simulated stress.",
    }))

    state = _make_state()
    card = _build_gallery_card_payload(state, str(tmp_path))

    assert "outcome" in card, "gallery card must always include the outcome key"
    assert card["outcome"] is not None
    assert card["outcome"]["label"] == "correct"
    assert "Aave" in card["outcome"]["outcome_summary"] or "Depegged" in card["outcome"]["outcome_summary"]


def test_gallery_card_outcome_is_none_without_artifact(tmp_path: Path):
    """No outcome.json ⇒ ``outcome: None`` on the card. The /explore grid
    branches on this to decide whether to render the pill."""
    from app.api.simulation import _build_gallery_card_payload

    state = _make_state()
    card = _build_gallery_card_payload(state, str(tmp_path))

    assert "outcome" in card
    assert card["outcome"] is None


def test_gallery_card_swallows_corrupt_outcome(tmp_path: Path):
    """A corrupt outcome.json must not blank out the gallery — one bad
    sim shouldn't blank out the whole /explore page (the same invariant
    that motivates ``test_malformed_artifact_json_is_swallowed`` over in
    ``test_unit_public_gallery``)."""
    from app.api.simulation import _build_gallery_card_payload

    (tmp_path / "outcome.json").write_text("{not json")

    state = _make_state()
    card = _build_gallery_card_payload(state, str(tmp_path))

    assert card["simulation_id"] == state.simulation_id
    assert card["outcome"] is None
