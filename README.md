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
    standings.py   # Passo 3: classificação dos grupos + melhores terceiros
    bracket.py     # Passo 3: chaveamento oficial (32-avos -> final) + progressão
    tournament.py  # Passo 3: orquestração do torneio completo + relatório
    montecarlo.py  # Passo 4: milhares de torneios -> probabilidades por fase
  data/
    ratings.py     # sorteio oficial + Elo calibrado + fixtures dos grupos
    providers/     # camada de dados: estática | feed local | (API em produção)
  service/
    engine.py      # motor 'vivo': aplica resultados reais -> recalcula tudo
tests/
  test_simulator.py  test_tournament.py  test_montecarlo.py  test_engine.py
```

## Dados ao vivo (o que torna o sistema "vivo")

A frescura dos dados vem de um **`DataProvider`** (`src/data/providers/`), a
única fronteira com a origem dos dados:

- **`StaticProvider`** — dados embutidos (default, offline).
- **`LocalFeedProvider`** — lê resultados e resoluções de playoff de um JSON
  (`data/sample_feed.json`); modelo para um provedor de API real.
- *Produção:* implementar a mesma interface sobre API-FOOTBALL / football-data.org.

O **`PredictionEngine`** (`src/service/engine.py`) liga o provedor ao modelo:
aplica os resultados reais, resolve as vagas de playoff e **recalcula** a
classificação, o chaveamento e a progressão. É o núcleo que o servidor MCP
(Passo 5) irá expor via `get_phase_predictions` e `update_real_score`.

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
python -m src.model.simulator    # demo: previsões da 1ª rodada dos grupos
python -m src.model.tournament   # torneio completo: grupos -> chaveamento -> campeão
python -m src.model.montecarlo   # 10.000 torneios -> probabilidades por fase
python -m pytest -q              # testes
```

## Estado / Roadmap

- [x] **Passo 1** — Estruturas de dados (`schemas.py`).
- [x] **Passo 2** — Esqueleto do simulador para a 1ª rodada dos grupos.
- [x] **Dados reais** — sorteio oficial + Elo calibrado (920 jogos) ligados.
- [x] **Passo 3** — Classificação + 8 melhores terceiros → chaveamento oficial
  → progressão até à final (`standings.py`, `bracket.py`, `tournament.py`).
- [x] **Passo 4** — Monte Carlo: milhares de torneios → probabilidades de
  avançar/oitavas/quartas/semis/final/título por seleção (`montecarlo.py`).
- [x] **Camada de dados 'viva'** — `DataProvider` + `PredictionEngine`: aplica
  resultados reais e resolve playoffs, recalculando todas as fases.
- [ ] Passo 5 — Servidor MCP (`fastmcp`) por cima do `PredictionEngine`.
- [ ] Passo 6 — Provedor de API real (API-FOOTBALL/football-data.org) +
  recalibração do Elo; condicionar o Monte Carlo aos jogos já disputados.

> ℹ️ Seis vagas de playoff (UEFA A–D, Intercontinentais 1–2) entram como
> placeholders com Elo provisório; previsões que as envolvem são marcadas como
> PROVISÓRIAS até os playoffs serem disputados.

> ⚠️ **Modo determinístico vs. Monte Carlo:** o chaveamento atual usa o
> resultado *mais provável* de cada jogo, pelo que o favorito vence sempre e a
> classificação fica "limpa" (1º com 9 pts, etc.). Isto dá o cenário modal,
> não as probabilidades reais de avanço/título — é exatamente o que o **Passo 4**
> (Monte Carlo, milhares de torneios) introduz, gerando os graus de confiança
> de cada seleção chegar a cada fase.
