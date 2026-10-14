#!/usr/bin/env bash
# Trusted host-side script; install separately, never execute it from an artifact.
set -Eeuo pipefail
umask 077
archive=${1:?Usage: deploy-ai.sh /absolute/path/package.tgz}
deploy=/opt/cxj-blog/deploy
cd "$deploy"
for tool in docker python3 flock mktemp; do command -v "$tool" >/dev/null; done
for file in .env .env.ai .env.ai-migrate docker-compose.prod.yml; do test -f "$deploy/$file"; done
exec 9>"$deploy/.deployment.lock"
flock -w 600 9
base=(docker compose --project-directory "$deploy" --env-file "$deploy/.env" -f "$deploy/docker-compose.prod.yml")
for extra in docker-compose.flow.yml docker-compose.frontend.flow.yml docker-compose.postgres.vector.yml; do
  if test -f "$deploy/$extra"; then base+=(-f "$deploy/$extra"); fi
done
override="$deploy/docker-compose.ai.flow.yml"
dc=("${base[@]}")
if test -f "$override"; then dc+=(-f "$override"); fi
"${dc[@]}" config --quiet
# pgvector must already be installed in the matching production PG16 container.
"${dc[@]}" exec -T postgres sh -c 'exec psql -X -w -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -v ON_ERROR_STOP=1 -c "SELECT default_version FROM pg_available_extensions WHERE name=\$\$vector\$\$"' | grep -q '^0.8.7$'
mkdir -p "$deploy/releases" "$deploy/backups"
release=$(mktemp -d "$deploy/releases/ai-XXXXXXXX")
new_image=$(python3 "$deploy/ai-artifact.py" "$archive" "$release/artifact")
docker load -i "$release/artifact/image.tar" > "$release/image-load.log"
commit=${new_image##*:flow-}
test "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$new_image")" = "$commit"
old_image=''
if test -f "$override"; then
  cp "$override" "$release/previous-ai-compose.yml"
  old_id=$("${dc[@]}" ps -q ai)
  if test -n "$old_id"; then old_image=$(docker inspect --format '{{.Image}}' "$old_id"); fi
fi
backup="$deploy/backups/$(basename "$release").dump"
"${dc[@]}" exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup"
test -s "$backup"
"${dc[@]}" exec -T postgres pg_restore --list < "$backup" > /dev/null
schema_signature() {
  local exists
  exists=$("${base[@]}" exec -T postgres sh -c 'exec psql -X -w -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -v ON_ERROR_STOP=1 -c "SELECT to_regclass(\$\$ai.schema_migration\$\$) IS NOT NULL"')
  if test "$exists" = f; then printf none; return; fi
  "${base[@]}" exec -T postgres sh -c 'exec psql -X -w -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -v ON_ERROR_STOP=1 -c "SELECT coalesce(md5(string_agg(name || checksum,\$\$,\$\$ ORDER BY name)),\$\$empty\$\$) FROM ai.schema_migration"'
}
schema_before=$(schema_signature)
network=$("${base[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["networks"]["internal"]["name"])')
healthy() {
  for ((attempt=0;attempt<36;attempt++)); do
    if "${dc[@]}" exec -T ai python -c 'import os,httpx; r=httpx.get("http://127.0.0.1:8000/internal/readyz",headers={"Authorization":"Bearer "+os.environ["AI_INTERNAL_TOKEN"]},timeout=5,trust_env=False); raise SystemExit(0 if r.status_code==200 else 1)' 2>/dev/null; then return 0; fi
    sleep 5
  done
  return 1
}
rollback() {
  local result=$?
  trap - ERR
  set +e
  schema_after=$(schema_signature)
  if test -z "$schema_after" || test "$schema_before" != "$schema_after"; then
    echo "AI deployment failed after schema change; automatic image rollback blocked. Backup: $backup" >&2
    exit "$result"
  fi
  if test -n "$old_image" && test -f "$release/previous-ai-compose.yml"; then
    cp "$release/previous-ai-compose.yml" "$override"
    "${dc[@]}" up -d --no-deps --no-build --pull never ai
    healthy || echo 'AI rollback health check failed' >&2
  else
    if test -f "$override"; then "${dc[@]}" stop ai; fi
    echo 'First AI deployment failed; public AI must remain disabled' >&2
  fi
  exit "$result"
}
trap rollback ERR
# Provisioning credentials are attached only to this short-lived migration container.
docker run --rm --network "$network" --env-file "$deploy/.env.ai" --env-file "$deploy/.env.ai-migrate" "$new_image" python -m scripts.provision
# Initial migration enqueues all existing articles; the worker consumes published content.
tmp=$(mktemp "$deploy/.ai-compose-XXXXXXXX")
printf 'services:\n  ai:\n    image: "%s"\n' "$new_image" > "$tmp"
cat >> "$tmp" <<'YAML'
    restart: unless-stopped
    env_file:
      - /opt/cxj-blog/deploy/.env.ai
    networks:
      - internal
    read_only: true
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=64m,mode=1777
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    mem_limit: 512m
    cpus: 1.0
    healthcheck:
      test: [CMD, python, -c, "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3)"]
      interval: 15s
      timeout: 5s
      retries: 5
YAML
mv "$tmp" "$override"
dc=("${base[@]}" -f "$override")
"${dc[@]}" config --quiet
"${dc[@]}" up -d --no-deps --no-build --pull never ai
healthy
trap - ERR
printf 'AI_DEPLOY_SUCCESS image=%s backup=%s\n' "$new_image" "$backup"
