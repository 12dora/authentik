## Project Constraints

### Local Reverse-Proxy Runtime

This workspace is commonly tested through `https://auth.jiefakj.com`, which reverse-proxies
the local Docker Compose authentik server:

```bash
/Users/konata/.local/share/easyauth/authentik/compose.yml
```

The server and worker use the `authentik-dingtalk:local` image. They do not bind-mount this
source tree, so source edits and `web/dist` builds are not applied to the browser until the
image is rebuilt and the containers are recreated.

After changing code that is served by the local authentik runtime, especially Web UI code,
container code, API code, or static assets, do not claim that the change is applied until
the runtime has been refreshed or the response explicitly says it has only been built in
the source tree.

Use this local apply flow when the user is validating through `auth.jiefakj.com`:

```bash
cd /Users/konata/code/Authentik

DOCKER_IMAGE=authentik-dingtalk:local make docker

docker compose \
  -f /Users/konata/.local/share/easyauth/authentik/compose.yml \
  --env-file /Users/konata/.local/share/easyauth/authentik/.env \
  up -d --force-recreate server worker

for i in {1..20}; do
  health_state=$(docker inspect easyauth-authentik-server-1 --format '{{.State.Health.Status}}' 2>/dev/null || true)
  echo "$health_state"
  [ "$health_state" = healthy ] && break
  sleep 3
done
```

For frontend changes, verify the reverse proxy serves the new bundle entry with a no-cache
request, then hard-refresh the browser when the user is looking at an already-open page:

```bash
curl -fsSL -H 'Cache-Control: no-cache' \
  https://auth.jiefakj.com/static/dist/admin/AdminInterface-2026.5.6.js \
  | rg 'src/admin/sources/chunks'
```

If a fix only takes effect after page reload, perform or instruct a hard refresh and verify
the loaded page uses the new chunk or shows the new behavior. Browser console logs may retain
old errors; compare timestamps or loaded chunk names before treating them as current failures.

### Verification

Run the smallest relevant tests before rebuilding the local image. For Web UI changes, prefer
targeted Vitest files plus `npm run --prefix web lint:types`; add broader lint/build checks when
the changed surface is shared or risky.
