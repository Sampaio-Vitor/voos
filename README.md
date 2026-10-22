# voos

MCP de preços de passagens aéreas. Busca no Google Flights, em reais, e devolve o link de compra no site da própria companhia (Gol, Azul, LATAM...), já com o voo escolhido. Feito para o [Tião](https://github.com/Sampaio-Vitor/alaxe), mas serve a qualquer cliente MCP.

Não usa API paga nem chave: fala com os mesmos endpoints internos que o site do Google Flights usa, pela biblioteca [swoop](https://github.com/gobeyondpty/swoop). Por isso pode quebrar se o Google mudar algo; um workflow semanal testa a busca de verdade e avisa.

## Ferramentas

| Ferramenta | Faz |
|---|---|
| `buscar_voos` | Voos com preço. Origem e destino em IATA (`CNF`, `GRU`), data `AAAA-MM-DD`, e opcionais: `data_volta`, passageiros, cabine, `max_escalas`, `companhias`, faixa de horário da ida, ordem e limite. Cada voo vem com um `id`. |
| `opcoes_de_volta` | Para ida e volta: as voltas da ida escolhida, cada uma com o preço total do par. |
| `link_de_compra` | O link da companhia para o voo (ou o par) escolhido, com o preço do momento. |

Só ida: `buscar_voos` → `link_de_compra(id)`.
Ida e volta: `buscar_voos(data_volta=...)` → `opcoes_de_volta(id da ida)` → `link_de_compra(id da volta)`. É o mesmo fluxo do site: o preço da ida e volta depende do par, e pode sair bem mais barato que duas idas avulsas.

Exemplo de link devolvido:

```
https://www.voeazul.com.br/br/pt/home/selecao-voo?c[0].ds=CNF&c[0].std=11/20/2026&c[0].as=REC&...
```

Limites conhecidos:

- Só a oferta direta da companhia. As agências (maxmilhas, 123milhas, Decolar) só aparecem para um navegador de verdade.
- Os `id` ficam na memória por uma hora. Reiniciar o serviço obriga a buscar de novo.
- Preço é do momento; a companhia confirma no checkout.

## Rodar

```sh
uv sync
uv run voos                          # MCP em http://127.0.0.1:8790/mcp, saúde em /healthz
uv run ruff check . && uv run pytest # lint e testes, sem rede
uv run pytest -m ao_vivo             # busca de verdade no Google Flights
docker compose up -d --build         # a imagem, como em produção
```

## Produção

Roda no `homeserver`, só para a tailnet, em `https://homeserver.tail19def0.ts.net:8790/mcp`. Cada push na `main` testa, publica a imagem no GHCR e troca o container pela Tailscale. Configuração, primeira subida e volta de versão em [docs/deploy.md](docs/deploy.md). Como ligar no Tião: [docs/tiao.md](docs/tiao.md).
