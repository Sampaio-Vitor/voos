# Ligar no Tião

O Tião usa o serviço pelo hub, no app Passagens (decisões 0066 e 0080 do `alaxe`): `buscar_voos`, `opcoes_de_volta` e `link_de_compra` para as buscas e os links, e `precos_por_data` para a escolha de datas na tela.

- URL: `https://homeserver.tail19def0.ts.net:8790/mcp` (Streamable HTTP, sem sessão, respostas em JSON).
- Sem token: só a tailnet alcança a porta, e o serviço só lê preços públicos.
- O notebook resolve `homeserver.tail19def0.ts.net` pelo `/etc/hosts` (não usa MagicDNS) e o certificado do `tailscale serve` vale para esse nome.

## Pela conversa (Agent SDK)

O agente pode usar o servidor direto, como MCP remoto:

```python
mcp_servers = {"voos": {"type": "http", "url": "https://homeserver.tail19def0.ts.net:8790/mcp"}}
allowed_tools = [
    "mcp__voos__buscar_voos",
    "mcp__voos__opcoes_de_volta",
    "mcp__voos__link_de_compra",
    "mcp__voos__precos_por_data",
]
```

As instruções do servidor (enviadas no `initialize`) explicam ao modelo o fluxo de só ida e de ida e volta.

## Pelo hub (como o Notas)

Igual ao `apps/compras/notas.py`: abrir a sessão, `initialize`, `call_tool` e ler `structured_content`. As ferramentas devolvem JSON estruturado (com schema), então dá para desenhar os voos na tela sem passar pelo modelo. Sugestão para o `config.toml`:

```toml
[voos]
# MCP de preços de voos no homeserver, só na tailnet.
url = "https://homeserver.tail19def0.ts.net:8790/mcp"
```

## Conferir do notebook

```sh
ssh alaxe 'curl -s https://homeserver.tail19def0.ts.net:8790/healthz'
```

Se não responder, falta uma regra na policy da Tailscale liberando `tag:alaxe` para a porta 8790 do homeserver (veja [deploy.md](deploy.md)).
