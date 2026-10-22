#!/usr/bin/env bash
# Testa o deploy.sh num projeto Compose descartável (Linux com Docker e flock).
set -Eeuo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
work=$(mktemp -d)
project=voos-ci-$$
export DEPLOY_DIR=$work/app DEPLOY_PROJECT=$project DEPLOY_ALLOW_LOCAL=1 VOOS_URL_PUBLICA=
export VOOS_PORTA=${VOOS_PORTA:-18790}
cleanup() {
  docker compose --project-name "$project" --project-directory "$DEPLOY_DIR" \
    -f "$root/compose.yaml" down --remove-orphans > /dev/null 2>&1 || true
  docker image rm -f voos:ci-a voos:ci-b voos:ci-ruim > /dev/null 2>&1 || true
  rm -rf -- "$work"
}
trap cleanup EXIT

rev_a=$(printf 'a%.0s' {1..40})
rev_b=$(printf 'b%.0s' {1..40})
rev_c=$(printf 'c%.0s' {1..40})
docker build -q -t voos:ci-a --build-arg APP_REVISION="$rev_a" "$root" > /dev/null
docker build -q -t voos:ci-b --build-arg APP_REVISION="$rev_b" "$root" > /dev/null
# Rótulo de uma revisão, healthz de outra: o deploy tem de recusar e voltar.
printf 'FROM voos:ci-b\nLABEL org.opencontainers.image.revision=%s\n' "$rev_c" |
  docker build -q -t voos:ci-ruim - > /dev/null

release=$work/release
mkdir -p "$release"
cp "$root/scripts/deploy.sh" "$root/compose.yaml" "$release/"
saude() { curl -fsS "http://127.0.0.1:$VOOS_PORTA/healthz"; }

echo '1. primeiro deploy'
bash "$release/deploy.sh" voos:ci-a "$rev_a"
[[ $(saude) == *"$rev_a"* ]]

echo '2. atualização'
bash "$release/deploy.sh" voos:ci-b "$rev_b"
[[ $(saude) == *"$rev_b"* ]]
[[ $(sed -n 2p "$DEPLOY_DIR/.deploy/current") == "$rev_b" ]]

echo '3. imagem de outro commit é recusada sem mexer no ar'
if bash "$release/deploy.sh" voos:ci-a "$rev_b"; then exit 1; fi
[[ $(saude) == *"$rev_b"* ]]

echo '4. healthz errado volta a versão anterior'
if bash "$release/deploy.sh" voos:ci-ruim "$rev_c"; then exit 1; fi
[[ $(saude) == *"$rev_b"* ]]
[[ $(sed -n 2p "$DEPLOY_DIR/.deploy/current") == "$rev_b" ]]

echo 'deploy ok'
