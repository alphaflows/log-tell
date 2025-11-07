import subprocess
import re
import requests
import threading

# CONFIG ----------------------
CONTAINERS = [
    "manta-vllm-server",
    "manta-vllm-server-2",
]

ERROR_PATTERNS = re.compile(r"(error|exception)", re.IGNORECASE)

ALERT_WEBHOOK = "https://your-alert-endpoint.com/webhook"
# --------------------------------


def send_alert(container, line):
    payload = {
        "container": container,
        "log": line,
    }
    print("ALERT:", payload)
    try:
        requests.post(ALERT_WEBHOOK, json=payload, timeout=3)
    except:
        pass


def follow_container(container):
    cmd = ["docker", "logs", "-f", container]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    for line in process.stdout:
        if ERROR_PATTERNS.search(line):
            send_alert(container, line.strip())


def main():
    for container in CONTAINERS:
        t = threading.Thread(target=follow_container, args=(container,))
        t.daemon = True
        t.start()

    print("Monitoring started. Press Ctrl+C to stop.")
    while True:
        pass


if __name__ == "__main__":
    main()
