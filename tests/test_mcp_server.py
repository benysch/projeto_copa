"""Testes do servidor MCP (Passo 5), via cliente in-memory do fastmcp."""

from __future__ import annotations

import asyncio

from fastmcp import Client

from src.mcp_server import engine, mcp


def _call(tool: str, args: dict | None = None):
    async def run():
        async with Client(mcp) as c:
            res = await c.call_tool(tool, args or {})
            return res.data
    return asyncio.run(run())


def setup_function(_):
    """Estado limpo antes de cada teste (o motor MCP é global)."""
    engine.reset()


def test_all_tools_registered():
    async def run():
        async with Client(mcp) as c:
            return {t.name for t in await c.list_tools()}
    names = asyncio.run(run())
    assert {
        "get_phase_predictions", "update_real_score", "get_match",
        "get_group_standings", "get_title_probabilities", "resolve_playoff",
        "list_phases",
    } <= names


def test_get_phase_predictions_final():
    data = _call("get_phase_predictions", {"phase_name": "final"})
    assert data["phase"] == "final"
    assert data["match_count"] == 1
    pred = data["matches"][0]["prediction"]
    assert pred["expected_winner"] is not None
    assert 0 <= pred["confidence_level"] <= 100


def test_phase_alias_resolution():
    by_alias = _call("get_phase_predictions", {"phase_name": "oitavas"})
    by_value = _call("get_phase_predictions", {"phase_name": "round_of_16"})
    assert by_alias["phase"] == by_value["phase"] == "round_of_16"
    assert by_alias["match_count"] == 8


def test_group_stage_has_72():
    data = _call("get_phase_predictions", {"phase_name": "grupos"})
    assert data["match_count"] == 72


def test_matchday_filter():
    data = _call("get_phase_predictions", {"phase_name": "grupos", "matchday": 1})
    assert data["match_count"] == 24
    assert all(m["matchday"] == 1 for m in data["matches"])


def test_invalid_matchday_errors():
    import pytest
    with pytest.raises(Exception):
        _call("get_phase_predictions", {"phase_name": "grupos", "matchday": 9})


def test_update_real_score_recalculates():
    standings = _call("get_group_standings", {"group": "H"})
    assert standings["table"][0]["team"]["id"] == "ESP"
    _call("update_real_score", {"match_id": "H11", "home_goals": 0, "away_goals": 4})
    after = _call("get_group_standings", {"group": "H"})
    uru = next(r for r in after["table"] if r["team"]["id"] == "URU")
    assert uru["goals_for"] >= 4


def test_resolve_playoff_adjusts_team():
    """Sem placeholders restantes, a ferramenta serve para corrigir nome/Elo."""
    out = _call("resolve_playoff", {"slot_id": "BIH", "team_name": "Bósnia", "elo": 1610})
    assert out["resolved"] == "BIH"
    grp = _call("get_group_standings", {"group": "B"})
    names = [r["team"]["name"] for r in grp["table"]]
    assert "Bósnia" in names


def test_list_phases_counts():
    data = _call("list_phases")
    counts = {p["name"]: p["match_count"] for p in data["phases"]}
    assert counts["round_of_32"] == 16
    assert counts["final"] == 1


def test_unknown_phase_errors():
    import pytest
    with pytest.raises(Exception):
        _call("get_phase_predictions", {"phase_name": "inexistente"})
