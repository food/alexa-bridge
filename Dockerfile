FROM python:3.12-slim

# Node is only needed for the Claude Code CLI (skill "Schlau"); the version is pulled at build time.
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm tzdata \
 && npm install -g @anthropic-ai/claude-code \
 && rm -rf /var/lib/apt/lists/* /root/.npm

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bridge ./bridge

# claude keeps its sessions under $HOME/.claude, --resume needs them.
RUN useradd -m -u 1000 bridge
USER bridge
ENV HOME=/home/bridge PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "bridge.app:app", "--host", "0.0.0.0", "--port", "8000"]
