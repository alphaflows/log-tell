## Log detector demo (Fluent Bit ➜ OpenObserve)

This folder contains a self-contained stack for watching Docker container logs and triggering error alerts through OpenObserve. Fluent Bit tails every container JSON log file on the host, enriches each record with the host name, forwards everything to a searchable stream, and mirrors only the error/exception lines into a second stream that is optimized for alert rules.

### 1. Configure

All knobs are exposed as environment variables so the same config can run on your laptop or any backend host.

```bash
cd mega-test/log-tell
cp .env.example .env   # edit if you want different creds/endpoints
```

Key variables (see `.env.example` for defaults):

- `OO_HTTP_HOST` – hostname Fluent Bit hits (use `openobserve` when running the local stack, or your production load balancer).
- `OO_AUTH_HEADER` – `Basic <base64(email:password)>` that matches the OpenObserve user/tenant.
- `LOG_INCLUDE_REGEX` – which lines you want to ingest at all. Keep it broad (e.g. `(?i)(info|error|exception)`) during testing, switch to `(?i)(error|exception)` for production.
- `LOG_ALERT_REGEX` – subset of lines that should be duplicated into the dedicated `logs_alerts` dataset for alert rules.

### 2. Run the demo stack

```bash
# Shared docker network so containers can resolve each other as "openobserve"
docker network create log-tell 2>/dev/null || true

# OpenObserve (stores + alerts)
docker compose --env-file .env -f openobserve/docker-compose.yml up -d

# Fluent Bit (collector + router)
docker compose --env-file .env -f fluentbit/docker-compose.yml up -d
```

Because the Fluent Bit compose file uses the `fluent/fluent-bit:3.0.4-debug` image, you now have a shell for troubleshooting:

```bash
docker exec -it fluentbit /bin/bash -lc 'wc -l /var/lib/docker/containers/*/*-json.log'
```

If you already run OpenObserve elsewhere, skip the first compose command, set `OO_HTTP_HOST=host.docker.internal` (or your load balancer DNS), and launch Fluent Bit on every server that hosts containers.
On Linux hosts the `/var/lib/docker/containers` bind immediately exposes every container log file; on macOS/Windows that path is empty because Docker Desktop runs inside a VM, so rely on the Fluentd/Forward input described below to stream logs instead of tailing files.

### 3. Verify ingestion quickly

- Fluent Bit logs should alternate between the JSON response (`{"code":200,...}`) and `HTTP status=200`.
- OpenObserve APIs can confirm data even if the UI filters are off:

```bash
curl -su admin@example.com:StrongPasswordHere \
     -H 'Content-Type: application/json' \
     http://localhost:5080/api/default/logs/_search \
     -d '{"query":{"sql":"SELECT timestamp, log FROM logs ORDER BY timestamp DESC LIMIT 5"}}'
```

- In the UI (`http://localhost:5080`), pick organization `default`, stream `logs` for all messages, or `logs_alerts` for only the error/exception copies. Remember to widen the time range if your containers emit future timestamps.

### 4. Feeding logs from other Docker Compose stacks

Fluent Bit exposes the Fluentd/Forward input on `${FLB_FORWARD_PORT:-24224}` (TCP + UDP) and tags everything it receives as `logs.forward.*`. This is the most portable way to get logs off containers that live in other docker-compose projects.

**Option A – share the `log-tell` network (Linux servers / same Docker host):**

```yaml
# docker-compose.yml from your application
networks:
  manta:
    driver: bridge
  log-tell:
    external: true

services:
  manta-api:
    networks:
      - manta
      - log-tell
    logging:
      driver: fluentd
      options:
        fluentd-address: fluentbit:24224
        tag: manta-api
```

**Option B – use the published host port (Docker Desktop/macOS/Windows):**

```yaml
services:
  manta-api:
    logging:
      driver: fluentd
      options:
        fluentd-address: host.docker.internal:24224
        tag: manta-api
```

Every log line emitted by `manta-api` now flows through the same Fluent Bit filters, is indexed into the `logs` stream, and—if it matches `LOG_ALERT_REGEX`—is duplicated into `logs_alerts` for alert rules. Repeat the logging block for any other service you need.

### 5. Alerting flow

1. Go to **Alerts → New Alert → SQL** in OpenObserve.
2. Sample SQL for the error stream:

   ```sql
   SELECT count(*) AS error_count
   FROM logs_alerts
   WHERE $__timeFilter(timestamp)
   HAVING count(*) > 0
   ```

3. Attach your preferred notifier (email, Slack, webhook). During dry runs, temporarily broaden `LOG_ALERT_REGEX` so INFO lines trigger the alert instantly.

### 6. Adapting for production

- **Collector footprint**: Deploy the Fluent Bit container (or the same config file) on every backend host/VM, mounting `/var/lib/docker/containers` read-only. Point `OO_HTTP_HOST` at the central OpenObserve URL over TLS.
- **Multi-tenant streams**: Use a different `OO_STREAM_LOGS` per environment/app (e.g., `logs_prod`, `logs_stage`). OpenObserve keeps them separated while sharing storage.
- **Noise control**: tighten `LOG_INCLUDE_REGEX`, add additional `grep` filters, or use Fluent Bit’s `lua`/`modify` filters to parse structured JSON payloads before they hit OpenObserve.
- **Alternate backends**: If you prefer Loki, Elasticsearch, or any SIEM, swap the Fluent Bit output plugin while keeping the same filters. OpenObserve was chosen here because it bundles alerting and dashboards in one binary.

### 7. Troubleshooting checklist

- `docker logs fluentbit` – confirms connectivity/auth problems (`no upstream connections / getaddrinfo` = host name not reachable, `HTTP status=401` = auth header mismatch, `failed to accept connection` = Fluentd forward port busy).
- `docker exec -it fluentbit /bin/bash` – inspect `/var/lib/docker/containers` to ensure the host path is mounted and growing.
- `LOG_ALERT_REGEX` too strict? Set it temporarily to `(?i).*` to make sure the rewrite filter is working, then dial it back.
- UI still empty? Double-check the dataset dropdown plus time range; use the curl query above to make sure the data really exists.

With this setup, the team receives near-real-time error visibility: Fluent Bit fans logs out from every server, OpenObserve indexes + visualizes them, and alerts fire from the dedicated `logs_alerts` stream. Adjust the environment variables per host/environment and the same files become your production logging pipeline.
