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

import math
from datetime import date, timedelta

from ..data.calendar import parse_kickoff
from ..data.polymarket import implied_title_probabilities
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
        self._base_forms: dict[str, float] = {}
        self._last_provider_results: dict[str, tuple[int, int]] = {}
        # Calibração pelo mercado (opcional/reversível): metadados + offsets
        # de Elo aplicados em form_modifier. None = modelo puro.
        self.market_calibration: dict | None = None
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
        self._base_forms = {tid: t.form_modifier for tid, t in self.teams.items()}
        self.market_calibration = None  # teams recarregados: offsets perdidos
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

    def matches_between(self, start: "date", days: int = 5) -> list[Match]:
        """Jogos (todas as fases) com kickoff em [start, start + days), por data."""
        end = start + timedelta(days=days)
        pool = self.group_matches + [
            m for matches in self.rounds.values() for m in matches
        ]
        selected = [
            (parse_kickoff(m.kickoff_utc), m)
            for m in pool
            if m.kickoff_utc and start <= parse_kickoff(m.kickoff_utc).date() < end
        ]
        selected.sort(key=lambda km: km[0])
        return [m for _, m in selected]

    def sample_scenario(self, seed: int | None = None) -> dict:
        """UM torneio completo sorteado da distribuição do modelo.

        Complementa `probabilities`: em vez de agregados, devolve um cenário
        plausível jogo a jogo (com empates, zebras e goleadas na frequência
        esperada), condicionado aos jogos de grupo já disputados.
        """
        return sample_scenario(
            self.teams, self.group_matches, params=self.params, seed=seed
        )

    def market_comparison(
        self,
        n_sims: int = 10_000,
        blend_weight: float = 0.5,
        seed: int | None = None,
        market_probs: dict[str, float] | None = None,
        market_vig_pct: float | None = None,
    ) -> dict:
        """Compara o título: modelo (Monte Carlo) x mercado (Polymarket).

        `blend_weight` é o peso do MODELO no pool logarítmico
        (p_blend ∝ p_modelo^w · p_mercado^(1-w), renormalizado): 1.0 = só
        modelo, 0.0 = só mercado. `market_probs` permite injetar dados
        (testes); default busca do Polymarket (rede).
        """
        if not 0.0 <= blend_weight <= 1.0:
            raise ValueError("blend_weight deve estar em [0, 1].")
        if market_probs is None:
            market_probs, market_vig_pct = implied_title_probabilities()

        mc = self.probabilities(n_sims=n_sims, seed=seed)
        model = {
            tid: probs["champion"] / 100.0
            for tid, probs in mc.probabilities.items()
        }

        # Pool logarítmico sobre as seleções presentes em ambas as fontes.
        eps = 1e-6  # evita zerar quem tem 0.0% numa única fonte
        common = [tid for tid in model if tid in market_probs]
        blended = {
            tid: (model[tid] + eps) ** blend_weight
            * (market_probs[tid] + eps) ** (1.0 - blend_weight)
            for tid in common
        }
        total = sum(blended.values())
        blended = {tid: v / total for tid, v in blended.items()}

        rows = [
            {
                "team_id": tid,
                "model_pct": round(model[tid] * 100, 1),
                "market_pct": round(market_probs[tid] * 100, 1),
                "blend_pct": round(blended[tid] * 100, 1),
                # Edge: quanto o modelo vê a mais (+) ou a menos (-) que o
                # mercado, em pontos percentuais.
                "edge_pp": round((model[tid] - market_probs[tid]) * 100, 1),
            }
            for tid in common
        ]
        rows.sort(key=lambda r: r["blend_pct"], reverse=True)
        return {
            "n_sims": mc.n_sims,
            "blend_weight": blend_weight,
            "market_vig_pct": market_vig_pct,
            # Se True, o "modelo" aqui JÁ está ancorado no mercado
            # (calibrate_to_market) — o edge perde o sentido de comparação pura.
            "market_calibration_active": self.market_calibration is not None,
            "teams": rows,
        }

    def calibrate_to_market(
        self,
        weight: float = 0.5,
        n_sims: int = 4_000,
        iterations: int = 8,
        learning_rate: float = 60.0,
        max_offset: float = 200.0,
        min_prob: float = 0.005,
        seed: int | None = None,
        market_probs: dict[str, float] | None = None,
        market_vig_pct: float | None = None,
    ) -> dict:
        """Ancora o modelo no mercado: ajusta ratings até o MC casar com o blend.

        Resolve o problema inverso por aproximações sucessivas: a cada iteração,
        o offset de Elo de cada seleção (aplicado em `form_modifier`) é nudged
        por `learning_rate * log(alvo / atingido)`, onde o alvo é o pool
        logarítmico modelo^w · mercado^(1-w) calculado sobre o modelo PURO.

        `weight` é o peso do MODELO (1.0 = puro, sem efeito; 0.0 = só mercado).
        Só são ajustadas seleções com probabilidade de título >= `min_prob`
        em alguma das fontes: abaixo disso o preço é piso de mercado/ruído de
        amostragem, sem informação sobre a força da equipe — o rating fica puro.
        Reversível via `reset_market_calibration()`; chamar de novo recalibra
        do zero (sem acumular offsets). `market_probs` permite injetar dados
        (testes); default busca do Polymarket (rede).
        """
        if not 0.0 <= weight <= 1.0:
            raise ValueError("weight deve estar em [0, 1].")
        if market_probs is None:
            market_probs, market_vig_pct = implied_title_probabilities()

        # Sempre parte do modelo PURO: remove qualquer calibração anterior.
        self.reset_market_calibration(refresh=False)

        eps = 1e-6
        base = self.probabilities(n_sims=n_sims, seed=seed)
        model = {
            tid: base.probabilities[tid]["champion"] / 100.0
            for tid in self.teams
        }
        common = [tid for tid in model if tid in market_probs]
        raw = {
            tid: (model[tid] + eps) ** weight
            * (market_probs[tid] + eps) ** (1.0 - weight)
            for tid in common
        }
        z = sum(raw.values())
        target = {tid: v / z for tid, v in raw.items()}
        # Só calibra onde há sinal: título relevante no modelo OU no mercado.
        adjustable = [
            tid for tid in common
            if model[tid] >= min_prob or market_probs[tid] >= min_prob
        ]

        offsets = {tid: 0.0 for tid in adjustable}
        achieved = {tid: model[tid] for tid in adjustable}
        for it in range(iterations):
            for tid in adjustable:
                step = learning_rate * math.log(
                    (target[tid] + eps) / (achieved[tid] + eps)
                )
                offsets[tid] = max(-max_offset, min(max_offset, offsets[tid] + step))
                self.teams[tid].form_modifier = self._base_forms[tid] + offsets[tid]
            mc = self.probabilities(
                n_sims=n_sims, seed=None if seed is None else seed + it + 1
            )
            achieved = {
                tid: mc.probabilities[tid]["champion"] / 100.0
                for tid in adjustable
            }

        # Distância de variação total ao alvo (qualidade da convergência).
        tv = 0.5 * sum(abs(achieved[tid] - target[tid]) for tid in adjustable)
        self.market_calibration = {
            "weight": weight,
            "market_vig_pct": market_vig_pct,
            "iterations": iterations,
            "n_sims": n_sims,
            "offsets": dict(offsets),
            "tv_distance_pct": round(tv * 100, 2),
        }
        self.refresh()  # re-prevê todos os jogos com os ratings ancorados

        movers = sorted(adjustable, key=lambda t: abs(offsets[t]), reverse=True)
        return {
            "weight": weight,
            "market_vig_pct": market_vig_pct,
            "iterations": iterations,
            "n_sims": n_sims,
            "adjusted_teams": len(adjustable),
            "tv_distance_pct": round(tv * 100, 2),
            "teams": [
                {
                    "team_id": tid,
                    "offset_elo": round(offsets[tid], 1),
                    "model_pure_pct": round(model[tid] * 100, 1),
                    "target_pct": round(target[tid] * 100, 1),
                    "achieved_pct": round(achieved[tid] * 100, 1),
                }
                for tid in movers
            ],
        }

    def reset_market_calibration(self, refresh: bool = True) -> dict:
        """Remove a âncora do mercado e volta ao modelo puro (Elo)."""
        was_active = self.market_calibration is not None
        offsets = (self.market_calibration or {}).get("offsets", {})
        for tid in offsets:
            if tid in self.teams:
                self.teams[tid].form_modifier = self._base_forms.get(tid, 0.0)
        self.market_calibration = None
        if refresh and was_active:
            self.refresh()
        return {"was_active": was_active}

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
