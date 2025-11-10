FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install requests

# Copy monitor script
COPY monitor.py .

# The monitoring agent needs access to docker CLI
RUN apt-get update && apt-get install -y docker.io && rm -rf /var/lib/apt/lists/*

CMD ["python", "monitor.py"]
