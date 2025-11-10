## Fluent Bit → OpenObserve (error-only)

This stack runs Fluent Bit sidecar-style on any Docker host, tails every container JSON log file under `/var/lib/docker/containers`, filters for error/exception/traceback lines, and forwards them to a central OpenObserve instance.

### 1. Configure
```bash
cd log-tell/fluentbit_monitor
cp .env.example .env
```

Update the following variables in `.env` to match your environment:

| Variable | Description | Example |
| --- | --- | --- |
| `OO_HTTP_HOST` / `OO_HTTP_PORT` / `OO_HTTP_TLS` | Remote OpenObserve endpoint (plain HTTP needs `tls=Off`). | `192.168.61.50`, `5080`, `Off` |
| `OO_ORG` / `OO_STREAM` | Organization + stream inside OpenObserve. | `default`, `default` |
| `OO_HTTP_USER` / `OO_HTTP_PASSWORD` | Credentials used for both the UI and ingestion API. | `admin@example.com`, `xTb6f8vsJJ4ZUjJh` |
| `LOG_ERROR_REGEX` | Case-insensitive regex applied to every Docker log line before shipping. | `(?i)(error|exception|traceback)` |
| `HOSTNAME` | Label stored with every record so you can tell which host emitted it. | `edge-a1` |

The `[OUTPUT]` block in `fluent-bit.conf` points directly at the remote host:

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

Adjust the values to suit your OpenObserve deployment—the structure stays the same.

### 2. Run the collector
```bash
docker compose --env-file .env up -d
```

Requirements:
- Runs best on Linux hosts where `/var/lib/docker/containers` contains the JSON log files. Mounts are read-only.
- Persistent offsets live under `fluentbit_monitor/state` to prevent log replay after restarts. Bind-mount it somewhere durable if needed.

### 3. What this config does
1. `tail` input watches every `*-json.log` file.
2. Multiline filter stitches stack traces so a traceback ships as one record.
3. `grep` filter keeps only lines that match `LOG_ERROR_REGEX`.
4. `record_modifier` injects the host name for filtering in OpenObserve.
5. HTTP output sends JSON payloads to `/api/${OO_ORG}/${OO_STREAM}/_json` with the supplied credentials, gzip compression, and `_timestamp` keys (the format OpenObserve expects).

No Fluentd forward input, duplicate streams, or shared Docker network are required—each host just needs outbound HTTP(S) reachability to OpenObserve.

### 4. Troubleshooting
- `docker logs fluentbit` – confirms connectivity/authentication (`HTTP status=401` = wrong credentials, `getaddrinfo` errors = host unreachable).
- `ls /var/lib/docker/containers` from inside the container to ensure the host path is mounted.
- Temporarily set `LOG_ERROR_REGEX=(?i).*` to confirm the filter is catching logs, then tighten it back down.
- Use the OpenObserve SQL API to verify data end-to-end:
  ```bash
  curl -su admin@example.com:xTb6f8vsJJ4ZUjJh \
       -H 'Content-Type: application/json' \
       http://192.168.61.50:5080/api/default/default/_search \
       -d '{"query":{"sql":"SELECT _timestamp, log FROM default ORDER BY _timestamp DESC LIMIT 5"}}'
  ```
