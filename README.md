# Log Tell Deployments

OpenObserve is the single log backend for this repo. Start it once on any reachable host and run collectors (Fluent Bit or the Python agent) on every Docker server that should report errors or tracebacks. All shippers emit to the same OpenObserve organization/stream, so alerting and dashboards stay centralized.

## Layout
- `openobserve/` – docker-compose stack for OpenObserve (UI, ingestion API, alerting).
- `fluentbit_monitor/` – Fluent Bit container that tails Docker JSON logs and forwards error/exception/traceback lines.
- `py_monitor/` – Lightweight Python agent that shells out to `docker logs -f` for specific containers and batches matches to OpenObserve.

## 1. Deploy OpenObserve once
Pick the machine that will host OpenObserve (example IP `192.168.61.50`). Every monitor will talk to it over HTTP/HTTPS.

```bash
cd openobserve
cp .env.example .env        # adjust ports + admin credentials
docker compose --env-file .env up -d
```

By default the UI/API live at `http://192.168.61.50:5080`. Record the username/password because the collectors authenticate with those values.
Set `OO_BASE_URL` to `http://<server-ip>:5080` (or `https://<server-name>` if fronted by TLS) so that alert emails/webhooks link back to the real host instead of `localhost`.
If you need OpenObserve to send email alerts, fill in the SMTP variables near the bottom of `openobserve/.env`—the example file shows a working Gmail/StartTLS configuration.

## 2. Fluent Bit option (Linux hosts)
Use Fluent Bit when you want a single container that tails every Docker JSON log file on a host and forwards only the important lines.

```bash
cd fluentbit_monitor
cp .env.example .env
# edit OO_* values so they point at the central OpenObserve instance
docker compose --env-file .env up -d
```

Key environment variables:

- `OO_HTTP_HOST` / `OO_HTTP_PORT` / `OO_HTTP_TLS` – remote OpenObserve host and port (`192.168.61.50:5080`, TLS Off for plain HTTP).
- `OO_ORG` / `OO_STREAM` – organization + stream under OpenObserve (`default/default`).
- `OO_HTTP_USER` / `OO_HTTP_PASSWORD` – same credentials you use in the OpenObserve UI.
- `LOG_ERROR_REGEX` – regex applied to Docker log lines (`(?i)(error|exception|traceback)` by default).

The resulting `[OUTPUT]` block mirrors the sample configuration you shared:

```
[OUTPUT]
  Name http
  Match docker.*
  URI /api/default/default/_json
  Host 192.168.61.50
  Port 5080
  tls Off
  Format json
  Json_date_key    _timestamp
  Json_date_format iso8601
  HTTP_User admin@example.com
  HTTP_Passwd xTb6f8vsJJ4ZUjJh
  compress gzip
```

Each host keeps its own tail offset under `fluentbit_monitor/state`, stitches multiline stack traces together, filters to the regex above, and ships matches straight to the remote OpenObserve endpoint—no shared Docker network required.

## 3. Python monitor option
Use the Python agent when you only need to watch a handful of containers and prefer a minimal footprint.

```bash
cd py_monitor
cp .env.example .env          # update MONITOR_CONTAINERS + OPENOBSERVE_URL
docker compose up -d
```

- `OPENOBSERVE_URL` should point at the same ingestion endpoint as Fluent Bit (example: `http://192.168.61.50:5080/api/default/default/_json`).
- `MONITOR_HOST` (optional) overrides the `host` field sent to OpenObserve if you want the logs labeled with the physical machine instead of the container hostname.
- `OPENOBSERVE_USER` / `OPENOBSERVE_PASSWORD` reuse your OpenObserve credentials.
- `ERROR_PATTERN` now defaults to `(?<!["'])ERROR(?![A-Za-z])`, so the agent only emits lines with uppercase `ERROR` tokens and skips JSON fragments such as `"error": null`.
- `TRACEBACK_PATTERN` and `TRACEBACK_MAX_LINES` control multi-line stack capture. Anything matching the traceback regex is buffered until the stack finishes and published once with severity `fatal`, while single-line `ERROR_PATTERN` hits keep severity `error` for separate alert routing.

Because the container only mounts the Docker socket, you can run it on any host that can reach `192.168.61.50:5080` (or whatever hostname/IP you configured) without joining custom Docker networks.

## 4. Verify ingestion
- **UI** – open `http://192.168.61.50:5080`, choose organization `default`, stream `default`, and widen the time range.
- **API** – confirm data directly with SQL:
  ```bash
  curl -su admin@example.com:xTb6f8vsJJ4ZUjJh \
       -H 'Content-Type: application/json' \
       http://192.168.61.50:5080/api/default/default/_search \
       -d '{"query":{"sql":"SELECT _timestamp, log FROM default ORDER BY _timestamp DESC LIMIT 5"}}'
  ```
- **Containers** – `docker logs openobserve`, `docker logs fluentbit`, or `docker logs logagent` surface issues on each host.

## 5. Alerts & notifications
Once Fluent Bit or the Python agent is pushing error lines into OpenObserve, wire up notifications directly in the OpenObserve UI:

1. **Create a notification channel** – Settings → Destinations → New Destination. Pick Email, Slack, or Webhook and provide the recipient/webhook token.
   Email destinations rely on the SMTP settings you configured in `openobserve/.env`.
2. **Define an alert** – Alerts → New Alert → SQL. A minimal query that triggers whenever new errors arrive:
   ```sql
   SELECT count(*) AS error_count
   FROM "default"
   WHERE $__timeFilter(_timestamp)
   HAVING count(*) > 0
   ```
3. **Set evaluation cadence** – choose how often to evaluate (e.g., every minute) and keep the threshold `error_count > 0` so every batch of errors notifies you immediately.
4. **Attach the notifier** – select the destination from step 1, enable notifications, and save. OpenObserve now emits alerts whenever matching records land from any monitor.

Create additional alerts for specific services (filter by `container` or `log LIKE '%OutOfMemory%'`) or route different severities to separate channels.

With OpenObserve running once and every monitor pointing to it over HTTP, you can deploy collectors on as many servers as you like while keeping alerting, storage, and dashboards centralized.

[How to Configure Email Alerts in OpenObserve: A Step-by-Step Guide](https://openobserve.ai/blog/how-to-configure-email-alerts-in-openobserve/)
