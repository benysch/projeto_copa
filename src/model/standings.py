"""Passo 3 (parte A) — Classificação dos grupos e melhores terceiros.

Calcula a tabela de cada grupo a partir dos resultados das partidas (reais,
quando inseridos; previstos, caso contrário) e aplica os critérios de desempate
da FIFA. Seleciona ainda os 8 melhores terceiros colocados, que completam as
32 vagas das eliminatórias no formato da Copa 2026.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import Match, Outcome, Team

WIN_POINTS, DRAW_POINTS = 3, 1


@dataclass
class TeamRecord:
    """Linha da tabela de um grupo para uma seleção."""

    team_id: str
    group: str
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0

    @property
    def points(self) -> int:
        return self.won * WIN_POINTS + self.drawn * DRAW_POINTS

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against

    def _add(self, scored: int, conceded: int) -> None:
        self.played += 1
        self.goals_for += scored
        self.goals_against += conceded
        if scored > conceded:
            self.won += 1
        elif scored == conceded:
            self.drawn += 1
        else:
            self.lost += 1


def _result_score(match: Match) -> tuple[int, int] | None:
    """Placar a usar: real se disponível, senão o previsto. None se nenhum."""
    if match.real_score is not None:
        return match.real_score.home_goals, match.real_score.away_goals
    if match.prediction is not None:
        s = match.prediction.predicted_score
        return s.home_goals, s.away_goals
    return None


def compute_group_table(
    matches: list[Match],
    team_ids: list[str],
    group: str,
    teams: dict[str, Team] | None = None,
) -> list[TeamRecord]:
    """Tabela ordenada de um grupo, com critérios de desempate da FIFA.

    Ordem de critérios: 1) pontos; 2) saldo de gols; 3) gols marcados;
    4) confronto direto (pontos entre os empatados); 5) Elo como tiebreaker
    determinístico final (substitui fair-play/sorteio do regulamento real).
    """
    records = {tid: TeamRecord(team_id=tid, group=group) for tid in team_ids}
    group_matches = [m for m in matches if m.group == group]

    for m in group_matches:
        score = _result_score(m)
        if score is None:
            continue
        hg, ag = score
        if m.home_team in records:
            records[m.home_team]._add(hg, ag)
        if m.away_team in records:
            records[m.away_team]._add(ag, hg)

    def elo_of(tid: str) -> float:
        return teams[tid].elo if teams and tid in teams else 0.0

    def primary_key(r: TeamRecord) -> tuple:
        return (r.points, r.goal_difference, r.goals_for)

    ordered = sorted(records.values(), key=primary_key, reverse=True)
    return _break_ties(ordered, group_matches, elo_of)


def _break_ties(ordered, group_matches, elo_of):
    """Resolve empates de (pontos, SG, GP) por confronto direto e depois Elo."""
    result: list[TeamRecord] = []
    i = 0
    while i < len(ordered):
        j = i
        key = (ordered[i].points, ordered[i].goal_difference, ordered[i].goals_for)
        while j < len(ordered) and (
            ordered[j].points,
            ordered[j].goal_difference,
            ordered[j].goals_for,
        ) == key:
            j += 1
        tied = ordered[i:j]
        if len(tied) > 1:
            tied = _sort_tied(tied, group_matches, elo_of)
        result.extend(tied)
        i = j
    return result


def _sort_tied(tied, group_matches, elo_of):
    """Mini-tabela de confronto direto entre os empatados; Elo como desempate."""
    ids = {r.team_id for r in tied}
    h2h = {r.team_id: [0, 0, 0] for r in tied}  # [pts, sg, gp]
    for m in group_matches:
        if m.home_team in ids and m.away_team in ids:
            score = _result_score(m)
            if score is None:
                continue
            hg, ag = score
            for tid, gf, ga in ((m.home_team, hg, ag), (m.away_team, ag, hg)):
                pts = WIN_POINTS if gf > ga else DRAW_POINTS if gf == ga else 0
                h2h[tid][0] += pts
                h2h[tid][1] += gf - ga
                h2h[tid][2] += gf
    return sorted(
        tied,
        key=lambda r: (h2h[r.team_id][0], h2h[r.team_id][1], h2h[r.team_id][2], elo_of(r.team_id)),
        reverse=True,
    )


@dataclass
class GroupStandings:
    """Resultado da classificação de todos os grupos."""

    tables: dict[str, list[TeamRecord]] = field(default_factory=dict)

    def position(self, group: str, place: int) -> TeamRecord:
        """1 = primeiro, 2 = segundo, 3 = terceiro de um grupo."""
        return self.tables[group][place - 1]

    def winners(self) -> dict[str, str]:
        return {g: t[0].team_id for g, t in self.tables.items()}

    def runners_up(self) -> dict[str, str]:
        return {g: t[1].team_id for g, t in self.tables.items()}

    def third_placed(self) -> list[TeamRecord]:
        return [t[2] for t in self.tables.values()]


def compute_all_standings(
    matches: list[Match],
    teams: dict[str, Team],
) -> GroupStandings:
    """Classifica todos os 12 grupos a partir das partidas fornecidas."""
    groups: dict[str, list[str]] = {}
    for tid, team in teams.items():
        if team.group:
            groups.setdefault(team.group, []).append(tid)

    standings = GroupStandings()
    for group, ids in sorted(groups.items()):
        standings.tables[group] = compute_group_table(matches, ids, group, teams)
    return standings


def best_third_placed(
    standings: GroupStandings,
    teams: dict[str, Team],
    count: int = 8,
) -> list[TeamRecord]:
    """Os `count` melhores terceiros colocados (8 no formato Copa 2026)."""
    thirds = standings.third_placed()
    thirds.sort(
        key=lambda r: (r.points, r.goal_difference, r.goals_for, teams[r.team_id].elo),
        reverse=True,
    )
    return thirds[:count]
