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
    ratings.py     # ratings Elo (ILUSTRATIVOS) e fixtures da 1ª rodada
tests/
  test_simulator.py
```

## Como correr

```bash
pip install -r requirements.txt
python -m src.model.simulator   # demo: previsões da 1ª rodada dos grupos
python -m pytest -q             # testes
```

## Estado / Roadmap

- [x] **Passo 1** — Estruturas de dados (`schemas.py`).
- [x] **Passo 2** — Esqueleto do simulador para a 1ª rodada dos grupos.
- [ ] Passo 3 — Classificação dos grupos + 8 melhores terceiros → chaveamento.
- [ ] Passo 4 — Propagação Monte Carlo das fases eliminatórias.
- [ ] Passo 5 — Servidor MCP (`fastmcp`) com as ferramentas.
- [ ] Passo 6 — Calibração dos ratings Elo com dados reais.

> ⚠️ Os ratings Elo e a composição dos grupos em `src/data/ratings.py` são
> **ilustrativos** para validar o pipeline. Substituir pelos dados oficiais do
> sorteio e por Elo calibrado antes de uso real.
