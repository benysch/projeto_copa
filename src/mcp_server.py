"""Passo 5 — Servidor MCP do sistema de previsão da Copa 2026.

Expõe o `PredictionEngine` (motor de estado vivo) como ferramentas MCP usando
`fastmcp`. Um cliente MCP (ex.: Claude) pode então consultar previsões por fase,
inserir resultados reais e obter probabilidades — tudo recalculado ao vivo.

Executar:
    python -m src.mcp_server            # stdio (default)
    fastmcp run src/mcp_server.py       # via CLI do fastmcp

Ferramentas:
    get_phase_predictions(phase_name)        -> previsões de uma fase
    get_bolao_picks(bolao, phase_name)       -> palpites ótimos p/ um bolão
    update_real_score(match_id, home, away)  -> insere resultado e recalcula
    get_match(match_id)                      -> detalhe de uma partida
    get_group_standings(group)               -> classificação de um grupo
    get_title_probabilities(top, n_sims)     -> probabilidades por fase (MC)
    simulate_tournament(seed)                -> UM cenário amostrado completo
    get_matches_by_date(start_date, days)    -> jogos da janela de datas
    get_market_odds(top, n_sims, blend)      -> modelo x Polymarket x blend
    calibrate_to_market(weight, ...)         -> ancora os ratings no mercado
    reset_market_calibration()               -> volta ao modelo puro
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


_BOLAO_ALIASES = {
    "pragma": "pragma", "copa_pragma": "pragma", "bolaoai": "pragma",
    "app": "app", "imagens": "app", "familia": "app",
}


@mcp.tool
def get_bolao_picks(
    bolao: str,
    phase_name: str,
    matchday: Optional[int] = None,
    top: int = 3,
) -> dict:
    """Palpites ÓTIMOS por valor esperado para um bolão, jogo a jogo.

    Diferente de `get_phase_predictions` (placar modal = mais provável), aqui
    cada palpite maximiza E[pontos] sob a função de pontos do bolão, calculado
    exatamente sobre a grade analítica de placares.

    `bolao`: "pragma" (Copa Pragma / bolaoai: 2 pts exato, 1 pt vencedor,
    multiplicador por fase + bónus de avanço no mata-mata) ou "app" (6/4/3/1 e
    empates 6/3; +3 por vencedor dos pênaltis em palpite de empate).

    Devolve, por jogo: os `top` melhores palpites com E[pontos] (incluindo quem
    avança / vencedor dos pênaltis quando aplicável), o E[pontos] do placar
    modal e o ganho do palpite ótimo sobre ele (`ev_gain_vs_modal`).
    """
    key = bolao.strip().lower().replace("-", "_").replace(" ", "_")
    if key not in _BOLAO_ALIASES:
        raise ValueError(f"Bolão desconhecido: '{bolao}'. Válidos: pragma, app")
    phase = _resolve_phase(phase_name)
    if matchday is not None and matchday not in (1, 2, 3):
        raise ValueError("matchday deve ser 1, 2 ou 3 (rodadas da fase de grupos).")
    picks = engine.bolao_picks(_BOLAO_ALIASES[key], phase, top=top, matchday=matchday)
    return {
        "bolao": _BOLAO_ALIASES[key],
        "phase": phase.value,
        "matchday": matchday,
        "match_count": len(picks),
        "matches": picks,
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
def get_matches_by_date(start_date: Optional[str] = None, days: int = 5) -> dict:
    """Jogos (todas as fases) com kickoff dentro da janela de datas, em ordem.

    `start_date` em ISO ("2026-06-11"); default = hoje (UTC). `days` é o tamanho
    da janela (default 5). Devolve previsões/resultados dos jogos no intervalo
    [start_date, start_date + days), ordenados por kickoff (UTC, calendário
    oficial FIFA). Nas eliminatórias os confrontos exibidos são os PREVISTOS
    pelo modelo enquanto as vagas não estão definidas.
    """
    from datetime import date as _date

    start = _date.fromisoformat(start_date) if start_date else _date.today()
    if days < 1:
        raise ValueError("days deve ser >= 1.")
    matches = engine.matches_between(start, days=days)
    return {
        "start_date": start.isoformat(),
        "days": days,
        "match_count": len(matches),
        "matches": [match_to_dict(m, engine.teams) for m in matches],
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
def get_market_odds(
    top: int = 16, n_sims: int = 10000, blend_weight: float = 0.5
) -> dict:
    """Título: nosso modelo x mercado (Polymarket) x estimativa combinada.

    Busca o evento 'world-cup-winner' do Polymarket (probabilidades implícitas
    nos preços, vig removido) e compara com o Monte Carlo do modelo. Devolve,
    por seleção: `model_pct`, `market_pct`, `blend_pct` (pool logarítmico com
    peso `blend_weight` no modelo) e `edge_pp` (modelo - mercado, em pontos
    percentuais; positivo = modelo mais otimista que o mercado).

    Requer acesso à internet; os preços têm cache de 5 minutos.
    """
    result = engine.market_comparison(n_sims=n_sims, blend_weight=blend_weight)
    result["teams"] = [
        {"team": _named(r.pop("team_id")), **r} for r in result["teams"][:top]
    ]
    return result


@mcp.tool
def calibrate_to_market(
    weight: float = 0.5, n_sims: int = 4000, iterations: int = 8
) -> dict:
    """ANCORA o modelo no mercado: todas as previsões passam a refletir o blend.

    Ajusta o rating efetivo de cada seleção (offset de Elo em form_modifier)
    até as probabilidades de título do Monte Carlo casarem com o pool
    logarítmico modelo^weight · mercado^(1-weight). A partir daí, TODAS as
    ferramentas (placar por jogo, cenários, grupos, título) usam os ratings
    ancorados. `weight`: 1.0 = modelo puro (sem efeito), 0.0 = só mercado.

    Reversível com reset_market_calibration(); chamar de novo recalibra do
    zero. Devolve os offsets aplicados e a qualidade da convergência
    (tv_distance_pct: distância de variação total ao alvo, em p.p.).
    Requer internet (Polymarket). Demora ~10-20s (iterations x n_sims).
    """
    out = engine.calibrate_to_market(
        weight=weight, n_sims=n_sims, iterations=iterations
    )
    out["teams"] = [
        {"team": _named(r.pop("team_id")), **r} for r in out["teams"][:16]
    ]
    return out


@mcp.tool
def reset_market_calibration() -> dict:
    """Remove a âncora do mercado: volta ao modelo puro (Elo + resultados reais)."""
    return engine.reset_market_calibration()


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
