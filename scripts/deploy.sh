#!/usr/bin/env bash
# Roda no homeserver, chamado pelo workflow Deploy por SSH. O token do GHCR, quando
# há, chega pelo stdin. O serviço não guarda estado: trocar o container é o deploy.
set -Eeuo pipefail
umask 077

image=${1:?uso: deploy.sh IMAGEM REVISAO [USUARIO_DO_REGISTRY]}
revision=${2:?falta o SHA completo do commit}
registry_user=${3:-}
[[ $revision =~ ^[a-f0-9]{40}$ ]] || { echo 'revisão inválida' >&2; exit 2; }
if [[ ! $image =~ ^ghcr\.io/[a-z0-9_./-]+@sha256:[a-f0-9]{64}$ ]]; then
  [[ ${DEPLOY_ALLOW_LOCAL:-0} == 1 && $image =~ ^voos:[a-z0-9.-]+$ ]] || {
    echo 'use um digest imutável do GHCR' >&2
    exit 2
  }
fi

app_dir=${DEPLOY_DIR:-$HOME/voos}
project=${DEPLOY_PROJECT:-voos}
port=${VOOS_PORTA:-8790}
public_url=${VOOS_URL_PUBLICA-https://homeserver.tail19def0.ts.net:$port}
release_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
state_dir=$app_dir/.deploy
mkdir -p "$state_dir"
exec 9>"$state_dir/deploy.lock"
flock -n 9 || { echo 'já existe um deploy rodando' >&2; exit 1; }

export DOCKER_CONFIG VOOS_IMAGE VOOS_PORTA=$port
DOCKER_CONFIG=$(mktemp -d)
previous_image=
switched=0
compose() {
  docker compose --project-name "$project" --project-directory "$app_dir" "$@"
}
recover() {
  local status=$?
  trap - EXIT INT TERM HUP
  if (( switched )) && [[ -n $previous_image && -f $state_dir/current.compose.yaml ]]; then
    echo 'deploy falhou; voltando a imagem anterior' >&2
    if VOOS_IMAGE=$previous_image compose -f "$state_dir/current.compose.yaml" \
      up -d --no-build --pull never --wait --wait-timeout 60; then
      echo 'versão anterior de volta' >&2
    else
      echo 'A VOLTA FALHOU: confira o container à mão' >&2
    fi
  fi
  rm -rf -- "$DOCKER_CONFIG"
  exit "$status"
}
trap recover EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

if [[ -n $registry_user ]]; then
  docker login ghcr.io --username "$registry_user" --password-stdin
fi
if [[ $image == ghcr.io/* ]]; then
  docker pull --quiet "$image"
fi
actual=$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
[[ $actual == "$revision" ]] || { echo 'a imagem não é do commit pedido' >&2; exit 1; }

VOOS_IMAGE=$image
compose -f "$release_dir/compose.yaml" config --quiet
running=$(compose -f "$release_dir/compose.yaml" ps --all --quiet voos)
if [[ -n $running ]]; then
  previous_image=$(docker inspect "$running" --format '{{.Image}}')
fi

switched=1
compose -f "$release_dir/compose.yaml" up -d --no-build --pull never --wait --wait-timeout 60 voos

# A revisão certa pelo loopback e, se configurado, pelo endereço da tailnet.
check() {
  local body
  body=$(curl --fail --silent --show-error --max-time 10 "$1/healthz")
  [[ $body == *"\"revisao\":\"$revision\""* ]] || { echo "healthz sem a revisão nova em $1" >&2; return 1; }
}
check "http://127.0.0.1:$port"
[[ -z $public_url ]] || check "$public_url"

cp "$release_dir/compose.yaml" "$state_dir/current.compose.yaml.tmp"
mv "$state_dir/current.compose.yaml.tmp" "$state_dir/current.compose.yaml"
printf '%s\n%s\n' "$image" "$revision" > "$state_dir/current.tmp"
mv "$state_dir/current.tmp" "$state_dir/current"
switched=0
echo "no ar: $revision ($image)"
