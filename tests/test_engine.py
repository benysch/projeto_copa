"""Testes do motor de estado 'vivo' e dos provedores de dados."""

from __future__ import annotations

import json

from src.data.providers import LocalFeedProvider, StaticProvider
from src.model.schemas import MatchStatus, Phase
from src.service.engine import PredictionEngine


def _feed(tmp_path, data: dict) -> str:
    path = tmp_path / "feed.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_static_engine_builds_full_tournament():
    eng = PredictionEngine(StaticProvider())
    assert len(eng.standings.tables) == 12
    assert len(eng.get_phase(Phase.ROUND_OF_32)) == 16
    assert eng.champion is not None


def test_local_feed_applies_real_results(tmp_path):
    path = _feed(tmp_path, {"results": {"A11": [3, 0]}})
    eng = PredictionEngine(LocalFeedProvider(path))
    a11 = eng._find_match("A11")
    assert a11.status is MatchStatus.FINISHED
    assert a11.real_score.home_goals == 3 and a11.real_score.away_goals == 0


def test_local_feed_resolves_playoffs(tmp_path):
    path = _feed(tmp_path, {"playoffs": {"UEFA-A": {"name": "Itália", "elo": 1860}}})
    eng = PredictionEngine(LocalFeedProvider(path))
    team = eng.teams["UEFA-A"]
    assert team.name == "Itália"
    assert team.is_placeholder is False
    assert team.elo == 1860


def test_update_real_score_recomputes_standings():
    eng = PredictionEngine(StaticProvider())
    # Espanha lidera o grupo H por previsão.
    assert eng.standings.position("H", 1).team_id == "ESP"
    # Insere uma goleada do Uruguai sobre a Espanha (H11 = ESP x URU).
    eng.update_real_score("H11", 0, 4)
    table_h = eng.standings.tables["H"]
    uru = next(r for r in table_h if r.team_id == "URU")
    assert uru.goals_for >= 4  # resultado real refletido na tabela


def test_manual_update_takes_priority_over_feed(tmp_path):
    path = _feed(tmp_path, {"results": {"A11": [3, 0]}})
    eng = PredictionEngine(LocalFeedProvider(path))
    eng.update_real_score("A11", 1, 1)  # corrige manualmente
    assert eng._find_match("A11").real_score.away_goals == 1


def test_unknown_match_update_raises():
    eng = PredictionEngine(StaticProvider())
    try:
        eng.update_real_score("ZZZ", 1, 0)
        assert False, "deveria ter levantado KeyError"
    except KeyError:
        pass


def test_provider_default_methods_empty():
    sp = StaticProvider()
    assert sp.fetch_results() == {}
    assert sp.resolve_placeholders() == {}


def test_api_provider_delegates_and_requires_key():
    from src.data.providers import ApiFootballProvider

    provider = ApiFootballProvider(league=1, season=2026, api_key=None)
    # Seleções/calendário vêm do provedor base, mesmo sem chave.
    assert len(provider.load_teams()) > 0
    assert len(provider.load_group_fixtures()) == 72
    # Buscar resultados sem chave deve falhar de forma clara.
    try:
        provider.fetch_results()
        assert False, "deveria exigir API_FOOTBALL_KEY"
    except RuntimeError:
        pass
