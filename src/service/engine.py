"""Motor de estado 'vivo' do torneio.

Liga um `DataProvider` ao motor de previsão e mantém o estado coerente: aplica
os resultados reais conhecidos, resolve as vagas de playoff e RECALCULA todas
as fases subsequentes. É a peça que torna o sistema verdadeiramente 'vivo' e a
fronteira que o servidor MCP (Passo 5) irá expor.

Fluxo:
    provider --(resultados/playoffs)--> aplica --> re-prevê jogos não disputados
        --> classificação --> chaveamento oficial --> progressão até à final
"""

from __future__ import annotations

from ..data.providers import DataProvider, StaticProvider
from ..model.bracket import build_round_of_32, simulate_knockouts
from ..model.montecarlo import MonteCarloResult, run_monte_carlo
from ..model.schemas import Match, Phase, Score, Team
from ..model.simulator import DEFAULT_PARAMS, ModelParams, predict_match
from ..model.standings import GroupStandings, compute_all_standings


class PredictionEngine:
    """Estado vivo do torneio: previsões que reagem aos resultados reais."""

    def __init__(
        self,
        provider: DataProvider | None = None,
        params: ModelParams = DEFAULT_PARAMS,
    ):
        self.provider = provider or StaticProvider()
        self.params = params
        self.teams: dict[str, Team] = {}
        self.group_matches: list[Match] = []
        self.standings: GroupStandings | None = None
        self.rounds: dict[Phase, list[Match]] = {}
        self._manual_results: dict[str, tuple[int, int]] = {}
        self.reload()

    # ------------------------------------------------------------------
    # Carregamento e recálculo
    # ------------------------------------------------------------------
    def reload(self) -> None:
        """(Re)carrega tudo do provedor e recalcula o torneio do zero.

        Mantém os resultados inseridos manualmente (`update_real_score`); use
        `reset()` para os descartar também.
        """
        self.teams = self.provider.load_teams()
        self.group_matches = self.provider.load_group_fixtures()
        self._apply_placeholder_resolutions()
        self.refresh()

    def reset(self) -> None:
        """Descarta os updates manuais e recarrega do provedor (estado limpo)."""
        self._manual_results.clear()
        self.reload()

    def refresh(self) -> None:
        """Reaplica os resultados conhecidos e recalcula todas as fases."""
        results = dict(self.provider.fetch_results())
        results.update(self._manual_results)  # updates manuais têm prioridade

        by_id = {m.match_id: m for m in self.group_matches}
        for mid, (hg, ag) in results.items():
            if mid in by_id:
                by_id[mid].set_real_score(hg, ag)

        # Prevê os jogos de grupo ainda não disputados.
        for m in self.group_matches:
            if not m.is_finished:
                m.prediction = predict_match(
                    self.teams[m.home_team], self.teams[m.away_team], self.params
                )
                m._sync_status()

        # Classificação -> chaveamento -> progressão eliminatória.
        # Resultados reais das eliminatórias ("m73"...) propagam-se pelas fases.
        self.standings = compute_all_standings(self.group_matches, self.teams)
        r32 = build_round_of_32(self.standings, self.teams)
        self.rounds = simulate_knockouts(
            r32, self.teams, self.params, real_results=results
        )

    def _apply_placeholder_resolutions(self) -> None:
        """Substitui vagas de playoff já decididas pela seleção real."""
        for slot_id, team in self.provider.resolve_placeholders().items():
            self.teams[slot_id] = team

    # ------------------------------------------------------------------
    # API pública (espelha as ferramentas MCP do Passo 5)
    # ------------------------------------------------------------------
    def get_phase(self, phase: Phase) -> list[Match]:
        """Jogos (com previsão e/ou resultado real) de uma fase."""
        if phase is Phase.GROUP_STAGE:
            return self.group_matches
        return self.rounds.get(phase, [])

    def update_real_score(self, match_id: str, home_goals: int, away_goals: int) -> Match:
        """Insere/atualiza um resultado real e recalcula as fases seguintes."""
        self._manual_results[match_id] = (int(home_goals), int(away_goals))
        self.refresh()
        match = self._find_match(match_id)
        if match is None:
            raise KeyError(f"Partida desconhecida: {match_id}")
        return match

    def probabilities(self, n_sims: int = 10_000, seed: int | None = None) -> MonteCarloResult:
        """Probabilidades de avanço/título por seleção (Monte Carlo).

        Condicionada aos jogos de grupo já disputados: os resultados reais são
        fixados e apenas os jogos por disputar são amostrados.
        """
        return run_monte_carlo(
            self.teams,
            n_sims=n_sims,
            params=self.params,
            seed=seed,
            group_matches=self.group_matches,
        )

    @property
    def champion(self) -> str | None:
        final = self.rounds.get(Phase.FINAL)
        return final[0].prediction.expected_winner if final else None

    # ------------------------------------------------------------------
    def _find_match(self, match_id: str) -> Match | None:
        for m in self.group_matches:
            if m.match_id == match_id:
                return m
        for matches in self.rounds.values():
            for m in matches:
                if m.match_id == match_id:
                    return m
        return None
