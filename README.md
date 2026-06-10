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
    serializers.py # partidas/previsões -> dicts JSON
  mcp_server.py    # Passo 5: servidor MCP (fastmcp) por cima do engine
tests/
  test_simulator.py   test_tournament.py   test_montecarlo.py
  test_engine.py      test_mcp_server.py
```

## Validação externa (benchmark)

O motor é validado contra o **SportIQ-MCP** (MIT, motor independente), alimentando
os MESMOS inputs (nossos ratings + sorteio oficial) aos dois e comparando as
probabilidades de título. Resultado: **diferença média ~0.3 pp** (máx ~3 pp) — dois
motores independentes concordam de perto, o que confirma a nossa implementação.

```bash
pip install -r requirements-dev.txt   # sportiq-mcp + scipy (opcionais)
python -m benchmarks.compare_sportiq
```

> SportIQ é só *referência*, não substitui o nosso motor (que é calibrado,
> transparente e específico para o nosso pipeline vivo).

## Servidor MCP

Expõe o motor vivo como ferramentas MCP (`get_phase_predictions`,
`update_real_score`, `get_group_standings`, `get_title_probabilities`,
`resolve_playoff`, `get_match`, `list_phases`, `sync_results`,
`get_elo_ratings`).

`get_phase_predictions(phase_name, matchday=None)` aceita um `matchday` opcional
(1–3) para filtrar a rodada na fase de grupos — ex.: `matchday=1` devolve só os
24 jogos da 1ª rodada. Cada jogo de grupos inclui o campo `matchday`.

`sync_results()` puxa os placares mais recentes da fonte ao vivo e recalcula
tudo; `get_elo_ratings(top)` mostra o Elo ATUAL de cada seleção (recalibrado
com os resultados reais) e o delta vs. o rating pré-torneio.

A fonte de dados escolhe-se pela variável de ambiente **`WC2026_PROVIDER`**:

| Valor | Provedor | Notas |
|---|---|---|
| `static` (default) | `StaticProvider` | offline; resultados via `update_real_score` |
| `livescore` | `LiveScoreMcpProvider` | ingestão automática e grátis (rede) |
| `feed` | `LocalFeedProvider` | JSON local (`WC2026_FEED_PATH`) |
| `api` | `ApiFootballProvider` | requer `API_FOOTBALL_KEY` |

```bash
python -m src.mcp_server          # stdio (default)
fastmcp run src/mcp_server.py     # via CLI do fastmcp
```

📄 Guia completo de execução local (venv + registo no Claude Code/Desktop):
**[`docs/RUNNING.md`](docs/RUNNING.md)**.

Configuração num cliente MCP (ex.: Claude Desktop), em `mcpServers`:

```json
{
  "mcpServers": {
    "wc2026": { "command": "python", "args": ["-m", "src.mcp_server"] }
  }
}
```

## Dados ao vivo (o que torna o sistema "vivo")

A frescura dos dados vem de um **`DataProvider`** (`src/data/providers/`), a
única fronteira com a origem dos dados:

- **`StaticProvider`** — dados embutidos (default, offline).
- **`LocalFeedProvider`** — lê resultados e resoluções de playoff de um JSON
  (`data/sample_feed.json`); modelo para um provedor de API real.
- **`ApiFootballProvider`** — template de produção para a API-FOOTBALL
  (api-sports.io): requer `API_FOOTBALL_KEY` e acesso de rede. Busca os
  resultados reais e mapeia-os para os nossos `match_id`.
- **`LiveScoreMcpProvider`** — ingestão AUTOMÁTICA e grátis agindo como cliente
  MCP de um servidor de placares (livescoremcp.com; protocolo real validado em
  2026-06-10). Percorre os dias da fase de grupos com `get_day_fixtures`,
  filtra a liga "FIFA World Cup", resolve nomes -> códigos e mapeia por par de
  seleções para os nossos `match_id` (orientação do placar corrigida); dias já
  encerrados são cacheados. Requer rede ao host. Eliminatórias entram via
  `update_real_score` (o par de seleções pode repetir-se no mata-mata).

O **`PredictionEngine`** (`src/service/engine.py`) liga o provedor ao modelo:
aplica os resultados reais (de grupos **e** eliminatórias), resolve as vagas de
playoff e **recalcula** a classificação, o chaveamento e a progressão. Os
resultados reais das eliminatórias propagam-se pelas fases — quem realmente venceu
avança, não o favorito previsto. É o núcleo que o servidor MCP expõe via
`get_phase_predictions` e `update_real_score`.

## Dados e modelo

- **Grupos (A–L):** composição do sorteio oficial da Copa 2026, com as seis
  vagas de playoff RESOLVIDAS com os vencedores reais de março/2026
  (A=Tchéquia, B=Bósnia, D=Türkiye, F=Suécia, I=Iraque, K=RD Congo).
- **Ratings Elo:** calibrados sobre ~920 internacionais reais (Out/2023–Mai/2026),
  ponderados por recência e importância da competição.
- **Recalibração contínua (`src/model/elo.py`):** cada resultado real atualiza
  os ratings com `delta = K·G·(W−We)` (convenção eloratings.net, K=50, G por
  margem de gols). Idempotente: a cada refresh os ratings repartem do snapshot
  pré-torneio e os deltas são reaplicados em ordem cronológica.
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
- [x] **Passo 5** — Servidor MCP (`fastmcp`) por cima do `PredictionEngine`
  (`src/mcp_server.py`), com 9 ferramentas.
- [x] **Monte Carlo condicionado** — a simulação parte dos jogos já disputados
  (resultados reais fixados, só o resto é amostrado).
- [x] **Template de provedor de API** — `ApiFootballProvider` pronto para ligar
  em produção (chave + rede).
- [x] **Vagas de playoff resolvidas** — vencedores reais de março/2026 nos
  dados base (BIH, SWE, TUR, CZE, COD, IRQ) com Elo de junho/2026.
- [x] **Fonte ao vivo ligada** — `LiveScoreMcpProvider` adaptado ao protocolo
  real do livescoremcp.com (validado contra o servidor); seleção por
  `WC2026_PROVIDER` + ferramenta `sync_results`.
- [x] **Recalibração contínua do Elo** — `src/model/elo.py` + integração
  idempotente no `PredictionEngine`; ferramenta `get_elo_ratings`.

> ⚠️ **Modo determinístico vs. Monte Carlo:** o chaveamento atual usa o
> resultado *mais provável* de cada jogo, pelo que o favorito vence sempre e a
> classificação fica "limpa" (1º com 9 pts, etc.). Isto dá o cenário modal,
> não as probabilidades reais de avanço/título — é exatamente o que o **Passo 4**
> (Monte Carlo, milhares de torneios) introduz, gerando os graus de confiança
> de cada seleção chegar a cada fase.
