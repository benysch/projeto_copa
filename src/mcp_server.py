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
    resolve_playoff(slot_id, name, elo)      -> define uma vaga de playoff
    list_phases()                            -> fases disponíveis e contagens
"""

from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

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

# Estado vivo partilhado por todas as ferramentas.
engine = PredictionEngine()

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
    return match_to_dict(match, engine.teams)


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
def resolve_playoff(slot_id: str, team_name: str, elo: float) -> dict:
    """Define uma vaga de playoff (ex.: 'UEFA-A') com a seleção que se classificou."""
    if slot_id not in engine.teams:
        raise ValueError(f"Slot desconhecido: '{slot_id}'.")
    team = engine.teams[slot_id]
    team.name = team_name
    team.elo = float(elo)
    team.is_placeholder = False
    engine.refresh()
    return {"resolved": slot_id, "name": team_name, "elo": elo}


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
