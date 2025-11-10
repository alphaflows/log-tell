import datetime
import logging
import os
import queue
import re
import socket
import subprocess
import threading
import time
from typing import List, Optional
from urllib.parse import urlparse

import requests


# ----------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------
DEFAULT_CONTAINERS = [
    "manta-vllm-server",
    "manta-vllm-server-2",
]


def _env_list(key: str, fallback: List[str]) -> List[str]:
    raw = os.getenv(key)
    if not raw:
        return fallback
    containers = [item.strip() for item in raw.split(",")]
    return [c for c in containers if c]


CONTAINERS = _env_list("MONITOR_CONTAINERS", DEFAULT_CONTAINERS)

ERROR_REGEX = os.getenv(
    "ERROR_PATTERN", r"(error|exception|traceback|critical|fail)"
)
ERROR_PATTERNS = re.compile(ERROR_REGEX, re.IGNORECASE)

OPENOBSERVE_URL = os.getenv(
    "OPENOBSERVE_URL", "http://openobserve:5080/api/default/logs/_json"
)
OPENOBSERVE_USER = os.getenv("OPENOBSERVE_USER", "admin@example.com")
OPENOBSERVE_PASSWORD = os.getenv("OPENOBSERVE_PASSWORD", "Admin123!")
OPENOBSERVE_AUTH: Optional[tuple[str, str]] = None
if OPENOBSERVE_USER and OPENOBSERVE_PASSWORD:
    OPENOBSERVE_AUTH = (OPENOBSERVE_USER, OPENOBSERVE_PASSWORD)

QUEUE_MAX_SIZE = int(os.getenv("QUEUE_MAX_SIZE", "2000"))
BATCH_MAX_SIZE = int(os.getenv("BATCH_MAX_SIZE", "50"))
BATCH_MAX_INTERVAL = float(os.getenv("BATCH_MAX_INTERVAL", "1"))
MAX_SEND_RETRIES = int(os.getenv("MAX_SEND_RETRIES", "6"))
SEND_BASE_BACKOFF = float(os.getenv("SEND_BASE_BACKOFF", "1.5"))
CONNECT_TIMEOUT = float(os.getenv("CONNECT_TIMEOUT", "2"))
READ_TIMEOUT = float(os.getenv("READ_TIMEOUT", "5"))
OPENOBSERVE_BOOT_TIMEOUT = float(os.getenv("OPENOBSERVE_BOOT_TIMEOUT", "120"))
OPENOBSERVE_BOOT_POLL = float(os.getenv("OPENOBSERVE_BOOT_POLL", "2"))
CONTAINER_RESTART_DELAY = float(os.getenv("CONTAINER_RESTART_DELAY", "3"))

LOG_QUEUE: "queue.Queue[dict]" = queue.Queue(maxsize=QUEUE_MAX_SIZE)
STOP_EVENT = threading.Event()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)


# ----------------------------------------------------
# UTILITIES
# ----------------------------------------------------
def wait_for_openobserve() -> bool:
    """Block until we can reach the OpenObserve TCP port."""
    parsed = urlparse(OPENOBSERVE_URL)
    host = parsed.hostname or "openobserve"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    deadline = time.time() + OPENOBSERVE_BOOT_TIMEOUT

    logging.info(
        "Waiting for OpenObserve at %s:%s (timeout=%ss)", host, port, OPENOBSERVE_BOOT_TIMEOUT
    )

    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT):
                logging.info("OpenObserve is reachable.")
                return True
        except OSError:
            logging.debug("OpenObserve not ready yet, retrying...")
            time.sleep(OPENOBSERVE_BOOT_POLL)

    logging.error("OpenObserve never became reachable before timeout.")
    return False


def enqueue_log(container: str, line: str) -> None:
    payload = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "container": container,
        "log": line,
        "severity": "error",
        "host": socket.gethostname(),
    }

    try:
        LOG_QUEUE.put(payload, timeout=1)
    except queue.Full:
        logging.warning("Dropping log; queue full (size=%s)", LOG_QUEUE.qsize())


def send_batch(batch: List[dict]) -> bool:
    """Send a batch of logs to OpenObserve with retries/backoff."""
    attempt = 1
    backoff = SEND_BASE_BACKOFF

    while attempt <= MAX_SEND_RETRIES and not STOP_EVENT.is_set():
        try:
            response = requests.post(
                OPENOBSERVE_URL,
                json=batch,
                auth=OPENOBSERVE_AUTH,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            if response.ok:
                logging.debug("Sent %s log(s) to OpenObserve", len(batch))
                return True

            logging.warning(
                "OpenObserve returned HTTP %s: %s", response.status_code, response.text
            )
        except requests.RequestException as exc:
            logging.warning("Send attempt %s failed: %s", attempt, exc)

        attempt += 1
        time.sleep(backoff)
        backoff *= 2

    logging.error("Failed to send %s log(s); dropping batch.", len(batch))
    return False


def sender_worker() -> None:
    logging.info("Sender worker started.")
    while not STOP_EVENT.is_set():
        try:
            first = LOG_QUEUE.get(timeout=0.5)
        except queue.Empty:
            continue

        if first is None:
            LOG_QUEUE.task_done()
            break

        batch = [first]
        batch_start = time.time()

        while len(batch) < BATCH_MAX_SIZE:
            timeout_left = BATCH_MAX_INTERVAL - (time.time() - batch_start)
            if timeout_left <= 0:
                break
            try:
                item = LOG_QUEUE.get(timeout=timeout_left)
                if item is None:
                    LOG_QUEUE.task_done()
                    STOP_EVENT.set()
                    break
                batch.append(item)
            except queue.Empty:
                break

        send_batch(batch)

        for _ in batch:
            LOG_QUEUE.task_done()

    logging.info("Sender worker exiting.")


# ----------------------------------------------------
# STREAM DOCKER LOGS
# ----------------------------------------------------
def follow_container(container: str) -> None:
    logging.info("Monitoring container %s", container)
    while not STOP_EVENT.is_set():
        # --tail 0 ensures we only stream fresh logs instead of replaying backlog.
        cmd = ["docker", "logs", "-f", "--tail", "0", container]
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            logging.error("docker CLI not found inside container.")
            return
        except Exception as exc:
            logging.error("Unable to start docker logs for %s: %s", container, exc)
            time.sleep(CONTAINER_RESTART_DELAY)
            continue

        assert process.stdout is not None
        for raw_line in process.stdout:
            if STOP_EVENT.is_set():
                break

            line = raw_line.strip()
            if not line:
                continue

            if ERROR_PATTERNS.search(line):
                enqueue_log(container, line)

        exit_code = process.wait()
        if STOP_EVENT.is_set():
            break

        if exit_code != 0:
            logging.warning(
                "docker logs exited for %s (code=%s); retrying after %.1fs",
                container,
                exit_code,
                CONTAINER_RESTART_DELAY,
            )
        time.sleep(CONTAINER_RESTART_DELAY)


# ----------------------------------------------------
# MAIN LOOP
# ----------------------------------------------------
def main() -> None:
    if not CONTAINERS:
        logging.error("No containers configured. Set MONITOR_CONTAINERS env var.")
        return

    logging.info(
        "Starting Docker log monitoring for %s", ", ".join(CONTAINERS)
    )

    wait_for_openobserve()

    sender_thread = threading.Thread(target=sender_worker, daemon=True)
    sender_thread.start()

    threads = []
    for container in CONTAINERS:
        t = threading.Thread(target=follow_container, args=(container,), daemon=True)
        t.start()
        threads.append(t)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutdown requested.")
    finally:
        STOP_EVENT.set()
        LOG_QUEUE.put(None)
        for t in threads:
            t.join(timeout=1)
        sender_thread.join(timeout=5)
        logging.info("Monitor stopped.")


if __name__ == "__main__":
    main()
