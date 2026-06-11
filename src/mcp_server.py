"""Passo 5 — Servidor MCP do sistema de previsão da Copa 2026.

Expõe o `PredictionEngine` (motor de estado vivo) como ferramentas MCP usando
`fastmcp`. Um cliente MCP (ex.: Claude) pode então consultar previsões por fase,
inserir resultados reais e obter probabilidades — tudo recalculado ao vivo.

Executar:
    python -m src.mcp_server            # stdio (default)
    fastmcp run src/mcp_server.py       # via CLI do fastmcp

Ferramentas:
    get_phase_predictions(phase_name)        -> previsões de uma fase
    update_real_score(match_id, home, away)  -> insere resultado e recalcula
    get_match(match_id)                      -> detalhe de uma partida
    get_group_standings(group)               -> classificação de um grupo
    get_title_probabilities(top, n_sims)     -> probabilidades por fase (MC)
    simulate_tournament(seed)                -> UM cenário amostrado completo
    resolve_playoff(slot_id, name, elo)      -> corrige nome/Elo de uma seleção
    list_phases()                            -> fases disponíveis e contagens
    sync_results()                           -> puxa placares da fonte ao vivo
    get_elo_ratings(top)                     -> Elo atual (recalibrado) + delta

Fonte de dados (variável de ambiente WC2026_PROVIDER):
    static (default) -> dados embutidos; resultados via update_real_score
    feed             -> JSON local (WC2026_FEED_PATH, default data/sample_feed.json)
    livescore        -> cliente MCP de placares (WC2026_LIVESCORE_URL)
    api              -> API-FOOTBALL (API_FOOTBALL_KEY, WC2026_API_LEAGUE/SEASON)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

from .data.providers import (
    ApiFootballProvider,
    DataProvider,
    LiveScoreMcpProvider,
    LocalFeedProvider,
    StaticProvider,
)
from .model.schemas import Phase
from .service.engine import PredictionEngine
from .service.serializers import match_to_dict

mcp = FastMCP(
    name="WC2026 Predictor",
    instructions=(
        "Sistema de previsão da Copa do Mundo 2026. Fornece placar previsto, "
        "vencedor e grau de confiança por partida, em todas as fases, e "
        "recalcula ao vivo quando resultados reais são inseridos."
    ),
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def build_provider() -> DataProvider:
    """Escolhe a fonte de dados pela variável de ambiente WC2026_PROVIDER."""
    kind = os.environ.get("WC2026_PROVIDER", "static").strip().lower()
    if kind == "feed":
        default_feed = _REPO_ROOT / "data" / "sample_feed.json"
        return LocalFeedProvider(os.environ.get("WC2026_FEED_PATH", str(default_feed)))
    if kind == "livescore":
        url = os.environ.get("WC2026_LIVESCORE_URL", "https://livescoremcp.com/sse")
        return LiveScoreMcpProvider(server_url=url)
    if kind == "api":
        return ApiFootballProvider(
            league=int(os.environ.get("WC2026_API_LEAGUE", "1")),
            season=int(os.environ.get("WC2026_API_SEASON", "2026")),
        )
    return StaticProvider()


# Estado vivo partilhado por todas as ferramentas.
engine = PredictionEngine(build_provider())

# Aceita o valor do enum ("group_stage") e aliases amigáveis (PT/EN).
_PHASE_ALIASES: dict[str, Phase] = {
    "group_stage": Phase.GROUP_STAGE, "grupos": Phase.GROUP_STAGE, "groups": Phase.GROUP_STAGE,
    "round_of_32": Phase.ROUND_OF_32, "32avos": Phase.ROUND_OF_32, "r32": Phase.ROUND_OF_32,
    "round_of_16": Phase.ROUND_OF_16, "oitavas": Phase.ROUND_OF_16, "r16": Phase.ROUND_OF_16,
    "quarter_finals": Phase.QUARTER_FINALS, "quartas": Phase.QUARTER_FINALS, "qf": Phase.QUARTER_FINALS,
    "semi_finals": Phase.SEMI_FINALS, "semis": Phase.SEMI_FINALS, "sf": Phase.SEMI_FINALS,
    "third_place": Phase.THIRD_PLACE, "terceiro": Phase.THIRD_PLACE, "3rd": Phase.THIRD_PLACE,
    "final": Phase.FINAL,
}


def _resolve_phase(phase_name: str) -> Phase:
    key = phase_name.strip().lower().replace("-", "_").replace(" ", "_")
    if key not in _PHASE_ALIASES:
        valid = sorted({p.value for p in Phase})
        raise ValueError(f"Fase desconhecida: '{phase_name}'. Válidas: {valid}")
    return _PHASE_ALIASES[key]


@mcp.tool
def get_phase_predictions(phase_name: str, matchday: Optional[int] = None) -> dict:
    """Previsões (placar, vencedor, confiança) dos jogos de uma fase.

    `phase_name` aceita o identificador ("group_stage", "round_of_32", ...) ou
    aliases ("grupos", "oitavas", "quartas", "semis", "final").

    `matchday` (opcional, 1–3) filtra a RODADA na fase de grupos — ex.: 1 devolve
    só a primeira rodada (24 jogos). Ignorado nas fases eliminatórias.
    """
    phase = _resolve_phase(phase_name)
    matches = engine.get_phase(phase)
    if matchday is not None:
        if matchday not in (1, 2, 3):
            raise ValueError("matchday deve ser 1, 2 ou 3 (rodadas da fase de grupos).")
        matches = [m for m in matches if m.matchday == matchday]
    return {
        "phase": phase.value,
        "matchday": matchday,
        "match_count": len(matches),
        "matches": [match_to_dict(m, engine.teams) for m in matches],
    }


@mcp.tool
def update_real_score(match_id: str, home_goals: int, away_goals: int) -> dict:
    """Insere o resultado REAL de uma partida e recalcula as fases seguintes."""
    match = engine.update_real_score(match_id, home_goals, away_goals)
    return {
        "updated": match_to_dict(match, engine.teams),
        "champion_now": _named(engine.champion),
    }


@mcp.tool
def get_match(match_id: str) -> dict:
    """Detalhe de uma partida específica (previsão e/ou resultado real)."""
    match = engine._find_match(match_id)
    if match is None:
        raise ValueError(f"Partida desconhecida: {match_id}")
    return match_to_dict(match, engine.teams, include_scorelines=True)


@mcp.tool
def get_group_standings(group: str) -> dict:
    """Classificação prevista de um grupo (A–L), com critérios de desempate."""
    group = group.strip().upper()
    if engine.standings is None or group not in engine.standings.tables:
        raise ValueError(f"Grupo desconhecido: '{group}'. Use A–L.")
    table = engine.standings.tables[group]
    return {
        "group": group,
        "table": [
            {
                "position": pos,
                "team": {"id": r.team_id, "name": engine.teams[r.team_id].name},
                "played": r.played,
                "points": r.points,
                "goal_difference": r.goal_difference,
                "goals_for": r.goals_for,
                "qualifies": pos <= 2,
            }
            for pos, r in enumerate(table, start=1)
        ],
    }


@mcp.tool
def get_title_probabilities(top: int = 16, n_sims: int = 10000) -> dict:
    """Probabilidades de avançar/oitavas/quartas/semis/final/título (Monte Carlo).

    Corre `n_sims` torneios completos. Use n_sims menor (ex.: 2000) para respostas
    mais rápidas com menor precisão.
    """
    result = engine.probabilities(n_sims=n_sims)
    return {
        "n_sims": result.n_sims,
        "teams": [
            {"team": {"id": tid, "name": engine.teams[tid].name}, **probs}
            for tid, probs in result.table(engine.teams, top=top)
        ],
    }


@mcp.tool
def simulate_tournament(seed: Optional[int] = None) -> dict:
    """Sorteia UM cenário completo e plausível do torneio (amostra de Monte Carlo).

    Ao contrário de get_phase_predictions — que mostra o desfecho MAIS PROVÁVEL
    de cada jogo (favoritos sempre vencem, quase nunca há empates, zebras ou
    goleadas) —, esta ferramenta AMOSTRA um torneio inteiro da distribuição do
    modelo: empates, zebras e goleadas aparecem na frequência estatisticamente
    esperada, sinalizados em `flags` e contabilizados em `summary`.

    Jogos de grupo já disputados ficam fixados (condicionamento 'vivo'). Cada
    chamada gera um cenário diferente; passe `seed` para reproduzir o mesmo.
    """
    return engine.sample_scenario(seed=seed)


@mcp.tool
def resolve_playoff(slot_id: str, team_name: str, elo: float) -> dict:
    """Corrige nome/Elo de uma seleção (as vagas de playoff já estão resolvidas).

    Historicamente definia uma vaga de playoff ('UEFA-A'); com todas as vagas
    resolvidas nos dados base, serve para ajustes manuais de rating.
    """
    if slot_id not in engine.teams:
        raise ValueError(f"Slot desconhecido: '{slot_id}'.")
    team = engine.teams[slot_id]
    team.name = team_name
    team.elo = float(elo)
    team.is_placeholder = False
    engine.refresh()
    return {"resolved": slot_id, "name": team_name, "elo": elo}


@mcp.tool
def sync_results() -> dict:
    """Puxa os resultados mais recentes da fonte ao vivo e recalcula tudo.

    Com WC2026_PROVIDER=livescore/feed/api, ingere os placares já disputados;
    com o provedor estático apenas reaplica os resultados manuais.
    """
    engine.refresh()
    finished_groups = sum(1 for m in engine.group_matches if m.is_finished)
    finished_knockouts = sum(
        1 for matches in engine.rounds.values() for m in matches if m.is_finished
    )
    return {
        "provider": type(engine.provider).__name__,
        "finished_group_matches": finished_groups,
        "finished_knockout_matches": finished_knockouts,
        "champion_now": _named(engine.champion),
    }


@mcp.tool
def get_elo_ratings(top: int = 48) -> dict:
    """Ratings Elo ATUAIS (recalibrados com os resultados reais) e delta vs. base.

    `delta_vs_base` mostra quanto cada seleção ganhou/perdeu de rating com os
    jogos já disputados do torneio (0 antes de a Copa começar).
    """
    ranked = sorted(engine.teams.values(), key=lambda t: t.elo, reverse=True)
    return {
        "recalibration_enabled": engine.recalibrate_elo,
        "k_factor": engine.elo_k,
        "teams": [
            {
                "id": t.team_id,
                "name": t.name,
                "group": t.group,
                "elo": round(t.elo, 1),
                "delta_vs_base": round(engine.elo_delta(t.team_id), 1),
            }
            for t in ranked[:top]
        ],
    }


@mcp.tool
def list_phases() -> dict:
    """Fases disponíveis e número de jogos em cada uma."""
    return {
        "phases": [
            {"name": p.value, "match_count": len(engine.get_phase(p))}
            for p in Phase
        ],
        "champion_prediction": _named(engine.champion),
    }


def _named(team_id: Optional[str]) -> Optional[dict]:
    if not team_id:
        return None
    return {"id": team_id, "name": engine.teams[team_id].name}


if __name__ == "__main__":  # pragma: no cover
    mcp.run()
