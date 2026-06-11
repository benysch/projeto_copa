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
from ..model import elo as elo_model
from ..model.bracket import build_round_of_32, simulate_knockouts
from ..model.montecarlo import MonteCarloResult, run_monte_carlo
from ..model.scenario import sample_scenario
from ..model.schemas import PHASE_ORDER, Match, Phase, Score, Team
from ..model.simulator import DEFAULT_PARAMS, ModelParams, predict_match
from ..model.standings import GroupStandings, compute_all_standings


class PredictionEngine:
    """Estado vivo do torneio: previsões que reagem aos resultados reais."""

    def __init__(
        self,
        provider: DataProvider | None = None,
        params: ModelParams = DEFAULT_PARAMS,
        recalibrate_elo: bool = True,
        elo_k: float = elo_model.WORLD_CUP_K,
    ):
        self.provider = provider or StaticProvider()
        self.params = params
        self.recalibrate_elo = recalibrate_elo
        self.elo_k = elo_k
        self.teams: dict[str, Team] = {}
        self.group_matches: list[Match] = []
        self.standings: GroupStandings | None = None
        self.rounds: dict[Phase, list[Match]] = {}
        self._manual_results: dict[str, tuple[int, int]] = {}
        self._base_elos: dict[str, float] = {}
        self._last_provider_results: dict[str, tuple[int, int]] = {}
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
        # Snapshot dos ratings pré-torneio: âncora da recalibração idempotente.
        self._base_elos = {tid: t.elo for tid, t in self.teams.items()}
        self.refresh()

    def reset(self) -> None:
        """Descarta os updates manuais e recarrega do provedor (estado limpo)."""
        self._manual_results.clear()
        self.reload()

    def refresh(self) -> None:
        """Reaplica os resultados conhecidos e recalcula todas as fases."""
        results = self._fetch_results_safe()
        results.update(self._manual_results)  # updates manuais têm prioridade

        # Reparte do snapshot pré-torneio: corrigir um resultado nunca deixa
        # resíduo no rating (a recalibração é reaplicada do zero a cada refresh).
        if self.recalibrate_elo:
            for tid, base in self._base_elos.items():
                if tid in self.teams:
                    self.teams[tid].elo = base

        by_id = {m.match_id: m for m in self.group_matches}
        for mid, (hg, ag) in results.items():
            if mid in by_id:
                by_id[mid].set_real_score(hg, ag)

        if self.recalibrate_elo:
            self._recalibrate_group_stage()

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

        # Resultados reais das eliminatórias também recalibram o Elo; as fases
        # seguintes são então re-previstas com os ratings atualizados. (Quem
        # joga cada eliminatória disputada é fixado pelos resultados reais,
        # pelo que uma única segunda passagem é estável.)
        if self.recalibrate_elo and self._recalibrate_knockouts():
            r32 = build_round_of_32(self.standings, self.teams)
            self.rounds = simulate_knockouts(
                r32, self.teams, self.params, real_results=results
            )

    def _fetch_results_safe(self) -> dict[str, tuple[int, int]]:
        """Busca resultados ao provedor, tolerando falhas transitórias.

        Se a fonte ao vivo (rede/feed) falhar, mantém o último estado conhecido
        em vez de derrubar o servidor.
        """
        try:
            self._last_provider_results = dict(self.provider.fetch_results())
        except Exception:
            pass  # fonte indisponível: usa o último snapshot bem-sucedido
        return dict(self._last_provider_results)

    def _recalibrate_group_stage(self) -> None:
        """Aplica os deltas de Elo dos jogos de grupo disputados, por rodada."""
        finished = [m for m in self.group_matches if m.is_finished]
        finished.sort(key=lambda m: (m.matchday or 0, m.match_id))
        for m in finished:
            self._apply_elo(m)

    def _recalibrate_knockouts(self) -> bool:
        """Aplica os deltas de Elo das eliminatórias disputadas; True se houve."""
        changed = False
        for phase in PHASE_ORDER:
            if phase is Phase.GROUP_STAGE:
                continue
            for m in self.rounds.get(phase, []):
                if m.is_finished:
                    self._apply_elo(m)
                    changed = True
        return changed

    def _apply_elo(self, m: Match) -> None:
        home, away = self.teams[m.home_team], self.teams[m.away_team]
        bonus = self.params.home_advantage if home.is_host else 0.0
        elo_model.apply_result(
            home, away,
            m.real_score.home_goals, m.real_score.away_goals,
            k=self.elo_k, home_bonus=bonus,
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

    def sample_scenario(self, seed: int | None = None) -> dict:
        """UM torneio completo sorteado da distribuição do modelo.

        Complementa `probabilities`: em vez de agregados, devolve um cenário
        plausível jogo a jogo (com empates, zebras e goleadas na frequência
        esperada), condicionado aos jogos de grupo já disputados.
        """
        return sample_scenario(
            self.teams, self.group_matches, params=self.params, seed=seed
        )

    def elo_delta(self, team_id: str) -> float:
        """Variação do Elo vs. o snapshot pré-torneio (recalibração ao vivo)."""
        team = self.teams[team_id]
        return team.elo - self._base_elos.get(team_id, team.elo)

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
