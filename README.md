# Projeto Copa — Sistema de Previsão da Copa do Mundo 2026

Sistema inteligente que gera, exibe e atualiza dinamicamente previsões para
cada partida da Copa 2026, produzindo três variáveis por jogo:

- **Placar Previsto** (`predicted_score`)
- **Vencedor** (`expected_winner`)
- **Grau de Confiança %** (`confidence_level`)

O pipeline é **vivo**: ao inserir resultados reais de uma rodada, os
chaveamentos seguintes e os graus de confiança são recalculados.

## Arquitetura

```
Elo  ->  gols esperados (λ)  ->  Poisson bivariada + Dixon-Coles  ->  grade de
placares  ->  [Placar, Vencedor, Confiança]   (+ Monte Carlo p/ avanço)
```

- **Linguagem:** Python
- **Motor:** Poisson bivariada com correção de Dixon-Coles + simulações de
  Monte Carlo para propagar o chaveamento.
- **Interface de dados:** servidor MCP (`fastmcp`) expondo
  `get_phase_predictions(phase_name)` e `update_real_score(match_id, score)`
  *(fase posterior do roadmap)*.

## Fases do torneio (formato 2026, 48 seleções)

| # | Fase | Enum |
|---|------|------|
| i | Fase de grupos (A–L) | `GROUP_STAGE` |
| ii | 32-avos (Round of 32) | `ROUND_OF_32` |
| iii | Oitavas | `ROUND_OF_16` |
| iv | Quartas | `QUARTER_FINALS` |
| v | Semifinais | `SEMI_FINALS` |
| vi | 3º lugar / Final | `THIRD_PLACE` / `FINAL` |

## Estrutura

```
src/
  model/
    schemas.py     # Passo 1: Match, MatchPrediction, Team, Phase, Status
    simulator.py   # Passo 2: motor Elo->Poisson/Dixon-Coles + Monte Carlo
  data/
    ratings.py     # sorteio oficial + Elo calibrado + fixtures da 1ª rodada
tests/
  test_simulator.py
```

## Dados e modelo

- **Grupos (A–L):** composição do sorteio oficial da Copa 2026. Seis vagas
  (playoffs UEFA A–D e Intercontinentais 1–2) ainda por definir entram como
  placeholders com Elo provisório, resolvidos quando os playoffs terminarem.
- **Ratings Elo:** calibrados sobre ~920 internacionais reais (Out/2023–Mai/2026),
  ponderados por recência e importância da competição.
- **Fórmula λ:** `clamp(1.35 + Δrating/400, [0.3, 3.5])`; Dixon-Coles ρ = −0.13.
- **Vantagem de casa:** +75 de Elo apenas para as anfitriãs (MEX/USA/CAN).

## Como correr

```bash
pip install -r requirements.txt
python -m src.model.simulator   # demo: previsões da 1ª rodada dos grupos
python -m pytest -q             # testes
```

## Estado / Roadmap

- [x] **Passo 1** — Estruturas de dados (`schemas.py`).
- [x] **Passo 2** — Esqueleto do simulador para a 1ª rodada dos grupos.
- [x] **Dados reais** — sorteio oficial + Elo calibrado (920 jogos) ligados.
- [ ] Passo 3 — Classificação dos grupos + 8 melhores terceiros → chaveamento.
- [ ] Passo 4 — Propagação Monte Carlo das fases eliminatórias.
- [ ] Passo 5 — Servidor MCP (`fastmcp`) com as ferramentas.
- [ ] Passo 6 — Resolver as 6 vagas de playoff e recalibrar com novos jogos.

> ℹ️ Seis vagas de playoff (UEFA A–D, Intercontinentais 1–2) entram como
> placeholders com Elo provisório; previsões que as envolvem são marcadas como
> PROVISÓRIAS até os playoffs serem disputados.
