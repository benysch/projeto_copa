"""Passo 1 — Estruturas de dados do sistema de previsão.

Define os schemas (Pydantic) que representam uma Partida, a sua Previsão
(placar previsto, vencedor esperado, grau de confiança) e o estado da fase.

Estes modelos são a fronteira de dados partilhada por todo o sistema: o
simulador (`src/model/simulator.py`) preenche-os e o servidor MCP (fase
posterior) serializa-os para `get_phase_predictions` / `update_real_score`.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enumerações
# ---------------------------------------------------------------------------
class Phase(str, Enum):
    """Fases cronológicas do torneio (formato Copa 2026, 48 seleções)."""

    GROUP_STAGE = "group_stage"        # (i)   12 grupos A–L
    ROUND_OF_32 = "round_of_32"        # (ii)  32-avos / primeira eliminatória
    ROUND_OF_16 = "round_of_16"        # (iii) oitavas de final
    QUARTER_FINALS = "quarter_finals"  # (iv)  quartas de final
    SEMI_FINALS = "semi_finals"        # (v)   semifinais
    THIRD_PLACE = "third_place"        # (vi)  disputa do terceiro lugar
    FINAL = "final"                    # (vi)  final

    @property
    def is_knockout(self) -> bool:
        """True para fases eliminatórias (não admitem empate no tempo normal)."""
        return self is not Phase.GROUP_STAGE


# Ordem cronológica usada para propagar resultados entre fases.
PHASE_ORDER: tuple[Phase, ...] = (
    Phase.GROUP_STAGE,
    Phase.ROUND_OF_32,
    Phase.ROUND_OF_16,
    Phase.QUARTER_FINALS,
    Phase.SEMI_FINALS,
    Phase.THIRD_PLACE,
    Phase.FINAL,
)


class MatchStatus(str, Enum):
    """Ciclo de vida de uma partida no pipeline 'vivo'."""

    SCHEDULED = "scheduled"    # agendada, ainda sem previsão calculada
    PREDICTED = "predicted"    # previsão calculada, resultado real pendente
    FINISHED = "finished"      # resultado real inserido (congela a previsão)


class Outcome(str, Enum):
    """Resultado de uma partida do ponto de vista da equipa da casa."""

    HOME = "home"
    DRAW = "draw"
    AWAY = "away"


# ---------------------------------------------------------------------------
# Entidades
# ---------------------------------------------------------------------------
class Team(BaseModel):
    """Seleção nacional com os ratings que alimentam o motor de Poisson."""

    team_id: str = Field(..., description="Código FIFA, ex.: 'BRA', 'ARG'.")
    name: str
    group: Optional[str] = Field(
        default=None, description="Letra do grupo (A–L); None nas eliminatórias."
    )
    # Força estatística (ver simulator.expected_goals).
    elo: float = Field(..., description="Rating Elo de futebol (base do modelo).")
    fifa_rank: Optional[int] = None
    # Ajuste de forma recente, em PONTOS DE ELO (somado ao rating). Ex.: +25/-25.
    form_modifier: float = Field(
        default=0.0, description="Ajuste de forma recente em pontos de Elo."
    )
    # Seleção anfitriã (México/EUA/Canadá) recebe vantagem de casa nos seus jogos.
    is_host: bool = Field(default=False, description="Anfitriã (vantagem de casa).")
    # Vaga ainda por definir (vencedor de playoff): Elo é provisório até resolver.
    is_placeholder: bool = Field(
        default=False, description="Vaga TBD (playoff) com Elo provisório."
    )

    def __str__(self) -> str:  # pragma: no cover - apenas apresentação
        return f"{self.name} ({self.team_id})"


class Score(BaseModel):
    """Placar de uma partida."""

    home_goals: int = Field(..., ge=0)
    away_goals: int = Field(..., ge=0)

    @property
    def outcome(self) -> Outcome:
        if self.home_goals > self.away_goals:
            return Outcome.HOME
        if self.home_goals < self.away_goals:
            return Outcome.AWAY
        return Outcome.DRAW

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.home_goals}-{self.away_goals}"


class MatchPrediction(BaseModel):
    """As três variáveis de saída exigidas, mais o detalhe probabilístico.

    `predicted_score`   -> Placar Previsto (moda da distribuição de placares).
    `expected_winner`   -> Vencedor (team_id) ou None em caso de empate previsto.
    `confidence_level`  -> Grau de Confiança em % (probabilidade do resultado
                           previsto, isto é, max(P_casa, P_empate, P_fora)).
    """

    predicted_score: Score
    expected_winner: Optional[str] = Field(
        default=None, description="team_id do vencedor previsto; None se empate."
    )
    confidence_level: float = Field(..., ge=0.0, le=100.0)

    # Distribuição completa de resultados (úteis para fases seguintes / debug).
    prob_home: float = Field(..., ge=0.0, le=1.0)
    prob_draw: float = Field(..., ge=0.0, le=1.0)
    prob_away: float = Field(..., ge=0.0, le=1.0)
    # Top placares mais prováveis: lista de ((casa, fora), probabilidade).
    top_scorelines: list[tuple[tuple[int, int], float]] = Field(default_factory=list)
    # Gols esperados (lambdas do modelo de Poisson) — diagnóstico.
    expected_goals_home: float = 0.0
    expected_goals_away: float = 0.0

    @property
    def predicted_outcome(self) -> Outcome:
        return self.predicted_score.outcome


class Match(BaseModel):
    """Uma partida do torneio em qualquer fase.

    Mantém tanto a previsão do modelo como o resultado real (quando inserido),
    permitindo o comportamento 'vivo': ao definir `real_score` a partida passa
    a FINISHED e as fases seguintes podem ser recalculadas.
    """

    match_id: str = Field(..., description="Identificador único, ex.: 'A1', 'R32-3'.")
    phase: Phase
    home_team: str = Field(..., description="team_id da equipa da casa/posição 1.")
    away_team: str = Field(..., description="team_id da equipa visitante/posição 2.")
    group: Optional[str] = None
    matchday: Optional[int] = Field(
        default=None, description="Rodada dentro da fase de grupos (1, 2 ou 3)."
    )
    kickoff_utc: Optional[str] = Field(default=None, description="ISO-8601 UTC.")

    status: MatchStatus = MatchStatus.SCHEDULED
    prediction: Optional[MatchPrediction] = None
    real_score: Optional[Score] = None

    @model_validator(mode="after")
    def _sync_status(self) -> "Match":
        """Garante coerência entre os campos preenchidos e o status."""
        if self.real_score is not None:
            self.status = MatchStatus.FINISHED
        elif self.prediction is not None and self.status == MatchStatus.SCHEDULED:
            self.status = MatchStatus.PREDICTED
        return self

    @property
    def is_finished(self) -> bool:
        return self.real_score is not None

    @property
    def actual_winner(self) -> Optional[str]:
        """team_id do vencedor real, ou None (empate ou jogo não disputado)."""
        if self.real_score is None:
            return None
        result = self.real_score.outcome
        if result is Outcome.HOME:
            return self.home_team
        if result is Outcome.AWAY:
            return self.away_team
        return None

    def set_real_score(self, home_goals: int, away_goals: int) -> "Match":
        """Insere o resultado real e congela a partida como FINISHED."""
        self.real_score = Score(home_goals=home_goals, away_goals=away_goals)
        self.status = MatchStatus.FINISHED
        return self
