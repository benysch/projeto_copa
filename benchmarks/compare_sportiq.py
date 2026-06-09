"""Benchmark externo: o nosso motor Monte Carlo vs. SportIQ-MCP.

NÃO substitui o nosso motor — é uma validação independente. Alimenta os MESMOS
inputs (os nossos ratings Elo + o sorteio oficial) aos dois motores e compara as
probabilidades de título. Diferenças pequenas e estáveis confirmam que a nossa
implementação está correta; isolando os inputs, qualquer divergência vem só do
motor (fórmula de λ, atribuição dos terceiros, resolução de empates).

SportIQ-MCP (https://github.com/Ninjabeam20/sportiq-mcp) é MIT. É uma dependência
OPCIONAL de desenvolvimento (ver requirements-dev.txt), não usada em produção:

    pip install -r requirements-dev.txt
    python -m benchmarks.compare_sportiq
"""

from __future__ import annotations

from collections import defaultdict

from src.data.ratings import build_teams
from src.model.montecarlo import run_monte_carlo


def _sportiq_probabilities(teams, n_sims: int, seed: int) -> dict[str, float]:
    """Probabilidades de título do SportIQ nos nossos inputs (win, em %)."""
    from sportiq.football.models.bracket_sim import simulate_tournament

    groups: dict[str, list[str]] = defaultdict(list)
    ratings: dict[str, float] = {}
    for tid, team in teams.items():
        groups[team.group].append(tid)
        ratings[tid] = team.elo
    groups = {g: groups[g] for g in sorted(groups)}

    out = simulate_tournament(groups, ratings, n_iter=n_sims, seed=seed)["teams"]
    return {tid: row["win"] * 100 for tid, row in out.items()}


def run_benchmark(n_sims: int = 10_000, seed: int = 42, top: int = 16) -> dict:
    """Compara os dois motores e devolve métricas de concordância."""
    teams = build_teams()
    ours = {
        tid: p["champion"]
        for tid, p in run_monte_carlo(teams, n_sims=n_sims, seed=seed).probabilities.items()
    }
    theirs = _sportiq_probabilities(teams, n_sims=n_sims, seed=seed)

    diffs = {tid: ours[tid] - theirs.get(tid, 0.0) for tid in teams}
    abs_diffs = [abs(d) for d in diffs.values()]
    return {
        "ours": ours,
        "theirs": theirs,
        "mean_abs_diff": sum(abs_diffs) / len(abs_diffs),
        "max_abs_diff": max(abs_diffs),
        "ranking": sorted(teams, key=lambda t: ours[t], reverse=True)[:top],
        "teams": teams,
    }


def _demo() -> None:  # pragma: no cover
    try:
        result = run_benchmark()
    except ModuleNotFoundError:
        print(
            "SportIQ não instalado. Instale o extra de benchmark:\n"
            "  pip install -r requirements-dev.txt"
        )
        return

    teams = result["teams"]
    print(f"\n=== BENCHMARK: nosso motor vs. SportIQ (mesmos inputs) ===\n")
    print(f"{'Seleção':<22}{'NÓS%':>8}{'SPORTIQ%':>10}{'Δ':>8}")
    print("-" * 48)
    for tid in result["ranking"]:
        o, s = result["ours"][tid], result["theirs"].get(tid, 0.0)
        print(f"{teams[tid].name:<22}{o:>7.1f}%{s:>9.1f}%{o - s:>+7.1f}")
    print(
        f"\nConcordância — diferença média absoluta: {result['mean_abs_diff']:.2f} pp; "
        f"máxima: {result['max_abs_diff']:.2f} pp"
    )
    print()


if __name__ == "__main__":  # pragma: no cover
    _demo()
