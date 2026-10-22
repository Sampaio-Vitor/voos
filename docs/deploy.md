# Deploy

Push na `main` → CI (ruff, pytest, shellcheck, actionlint e o teste do deploy) → imagem `linux/amd64` no GHCR, por SHA → o runner entra na tailnet como nó efêmero → SSH no `homeserver` → `scripts/deploy.sh` troca o container e confere o `/healthz` na revisão nova, pelo loopback e pela tailnet. Se a conferência falhar, volta a imagem anterior sozinho e o job fica vermelho. Mesmo modelo do Notas, mais simples porque o serviço não guarda estado.

## No servidor

| O quê | Onde |
|---|---|
| Container `voos-voos-1` (projeto Compose `voos`) | `127.0.0.1:8790`, read-only, sem capabilities, 256 MB |
| Estado do deploy | `~/voos/.deploy/current` (imagem e SHA), `current.compose.yaml`, `releases/<run>/` |
| Tailnet | `tailscale serve --bg --https=8790 http://127.0.0.1:8790` → `https://homeserver.tail19def0.ts.net:8790` (tailnet only, sem Funnel) |
| Chave do CI | `~/.ssh/authorized_keys`, entrada `voos-github-actions` com `from="100.64.0.0/10",restrict` |

Tirar da tailnet: `tailscale serve --https=8790 off`. Parar: `docker compose -p voos --project-directory ~/voos -f ~/voos/.deploy/current.compose.yaml down`.

## No GitHub (já configurado)

Environment `production`, só a branch `main`:

- Secret `DEPLOY_SSH_KEY`: chave Ed25519 dedicada (`voos-github-actions`; a cópia local é `~/.ssh/id_ed25519_voos_deploy` no Mac).
- Secret `DEPLOY_KNOWN_HOSTS`: host key Ed25519 do homeserver, lida pelo SSH já autenticado; impressão digital `SHA256:/K+v8I52ayN8Ua9GQrhEgqmR2gWu91GYqLwa9OR6ax0`.
- Variables `DEPLOY_HOST` = `homeserver.tail19def0.ts.net` e `DEPLOY_USER` = `vitor`.

## Tailscale (falta fazer, no painel)

Enquanto isso não existir, o push na `main` testa e publica a imagem, mas pula o job de deploy.

1. **Trust credential** (Settings → Trust credentials → identidade federada OIDC):
   - Issuer: `https://token.actions.githubusercontent.com`
   - Subject: `repo:Sampaio-Vitor@110466124/voos@1429763094:environment:production`
   - Scope: Auth Keys (write)
   - Tag: `tag:notas-ci`. Ela já tem `tcp:22` no homeserver, que é tudo de que o deploy precisa, então a policy não muda. Se preferir uma tag só deste repositório, crie `tag:voos-ci` em `tagOwners` e um grant `tag:voos-ci → 100.118.83.39 tcp:22`.
2. No GitHub, em Settings → Secrets and variables → Actions:
   - Environment `production`: secrets `TS_OAUTH_CLIENT_ID` (Client ID) e `TS_AUDIENCE` (Audience) da credencial.
   - **Repository** variable `TS_TAG` = a tag escolhida. Ela liga o job de deploy; precisa ser do repositório, porque o `if` do job é avaliado antes do environment.

   ```sh
   gh secret set TS_OAUTH_CLIENT_ID -R Sampaio-Vitor/voos --env production
   gh secret set TS_AUDIENCE -R Sampaio-Vitor/voos --env production
   gh variable set TS_TAG -R Sampaio-Vitor/voos --body tag:notas-ci
   gh workflow run Deploy -R Sampaio-Vitor/voos
   ```

3. **Tião:** a policy precisa deixar `tag:alaxe` chegar na porta 8790 do homeserver. Conferir com `ssh alaxe 'curl -s https://homeserver.tail19def0.ts.net:8790/healthz'`; se não responder, somar a porta ao grant que já libera o Notas (`tcp:4329`) para o notebook.

Se a troca de token falhar com `403`, confira o subject: ele usa os IDs numéricos (`gh api repos/Sampaio-Vitor/voos/actions/oidc/customization/sub --jq .sub_claim_prefix`, mais `:environment:production`).

## Voltar uma versão

Actions → Deploy → Run workflow na `main`, com o SHA completo de um commit já publicado em `revision`. Em branco, publica a `main` atual.

## Deploy à mão (sem o CI)

Do Mac, com o SSH de administração e uma imagem já publicada no GHCR:

```sh
sha=$(git rev-parse origin/main)
digest=$(docker buildx imagetools inspect ghcr.io/sampaio-vitor/voos:sha-$sha --format '{{.Manifest.Digest}}')
dir=voos/.deploy/releases/manual-$(date +%s)
ssh server "mkdir -p $dir" && scp scripts/deploy.sh compose.yaml server:$dir/
ssh server "bash $dir/deploy.sh ghcr.io/sampaio-vitor/voos@$digest $sha"
```
