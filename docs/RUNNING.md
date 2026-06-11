# Como rodar localmente (stdio)

A forma recomendada de usar este projeto: o cliente MCP (Claude Code / Claude
Desktop) arranca o servidor como subprocesso por **stdio**. Sem servidor, sem
custos, sem rede.

## 1. Pré-requisitos

- **Python 3.10+** (o código usa anotações `X | Y` e genéricos `list[...]`).

## 2. Instalar (ambiente virtual)

A partir da raiz do repositório:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> Usar um venv evita conflitos com pacotes do sistema (ex.: a instalação global
> do `fastmcp` pode colidir com um `PyJWT` do sistema).

## 3. Verificar que corre

```bash
python -m src.model.tournament    # torneio completo -> campeão previsto
python -m src.model.montecarlo    # probabilidades por fase (Monte Carlo)
python -m pytest -q               # 79 testes
python -m src.mcp_server          # arranca o servidor MCP (Ctrl-C para sair)
```

## 4. Registar o servidor MCP no cliente

O servidor é um módulo Python (`src.mcp_server`). Para que `src` seja importável
**independentemente do diretório** de onde o cliente o arranca, definimos
`PYTHONPATH` para a raiz do repositório e usamos o Python do venv por caminho
absoluto. Substitui `/CAMINHO/ABSOLUTO/projeto_copa` pelo teu caminho real.

### Claude Code (CLI)

```bash
claude mcp add wc2026 \
  --env PYTHONPATH=/CAMINHO/ABSOLUTO/projeto_copa \
  -- /CAMINHO/ABSOLUTO/projeto_copa/.venv/bin/python -m src.mcp_server
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "wc2026": {
      "command": "/CAMINHO/ABSOLUTO/projeto_copa/.venv/bin/python",
      "args": ["-m", "src.mcp_server"],
      "env": { "PYTHONPATH": "/CAMINHO/ABSOLUTO/projeto_copa" }
    }
  }
}
```

Reinicia o cliente. Depois podes pedir, por exemplo:
*"chama `get_phase_predictions` para a fase `final`"* ou
*"usa `update_real_score` para `H11` = 0-4 e mostra a nova classificação do grupo H"*.

## 5. Dados ao vivo (opcional)

Por omissão o servidor usa o `StaticProvider` (dados embutidos; o "vivo" funciona
por `update_real_score` manual). Para ingestão automática define a variável de
ambiente `WC2026_PROVIDER` no registo do servidor:

```bash
claude mcp add wc2026 \
  --env PYTHONPATH=/CAMINHO/ABSOLUTO/projeto_copa \
  --env WC2026_PROVIDER=livescore \
  -- /CAMINHO/ABSOLUTO/projeto_copa/.venv/bin/python -m src.mcp_server
```

Valores: `livescore` (placares grátis via livescoremcp.com, requer rede),
`feed` (JSON local, `WC2026_FEED_PATH`), `api` (API-FOOTBALL, requer
`API_FOOTBALL_KEY`), `static` (default). Com uma fonte ao vivo ativa, chama a
ferramenta `sync_results` para puxar os placares mais recentes e recalcular;
`get_elo_ratings` mostra o Elo recalibrado com os resultados reais.
Ver a secção *Dados ao vivo* no `README.md`.

## Porquê não Vercel / serverless?

Este é um servidor MCP **com estado em memória** (`PredictionEngine`) e cálculo
**CPU-bound** (Monte Carlo). Precisa de um processo persistente e de uma ligação
longa (stdio/SSE) — o oposto de funções serverless efémeras e stateless. Para
hospedar sempre-ligado, usa um **contentor persistente** (Railway / Fly.io /
Cloud Run) com o transporte HTTP/SSE do fastmcp, não a Vercel.
