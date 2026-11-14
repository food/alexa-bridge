"""Schlau: headless Claude Code (via claude-runner) with web search, memory and read-only Home Assistant."""
import requests

from . import config
from .conversation import recall, remember, system_prompt

# Home Assistant read-only: the switching tools of the HA MCP server stay blocked,
# Alexa keeps doing that herself.
HA_TOOLS = ["mcp__ha__homeassistant__GetLiveContext", "mcp__ha__todo__get_items"]
# Without this hint Haiku looked for house values in memory instead of Home Assistant.
HINT = (
    " Werte aus dem Haus (Strom, Gas, Wasser, Rechnungen, Abschlaege, Zaehler, Solaranlage, Geraete)"
    " holst du mit GetLiveContext aus Home Assistant. Wissen ueber den Nutzer, seine Projekte und"
    " seine Infrastruktur steht im Gedaechtnis (memory). Aktuelles aus der Welt per Websuche."
)
TIMEOUT = 300


def run_request(question, session):
    """Body for claude-runner's /v1/run: MCP servers, allowed tools, optional resume."""
    servers = {}
    if config.MEMORY_TOKEN:
        servers["memory"] = {"type": "http", "url": config.MEMORY_URL,
                             "headers": {"Authorization": f"Bearer {config.MEMORY_TOKEN}"}}
    if config.HA_TOKEN:
        servers["ha"] = {"type": "http", "url": f"{config.HA_URL}/api/mcp",
                         "headers": {"Authorization": f"Bearer {config.HA_TOKEN}"}}
    body = {"prompt": question, "system": system_prompt() + HINT, "model": config.CLAUDE_MODEL,
            "allowed_tools": ["WebSearch", "WebFetch", "mcp__memory"] + HA_TOOLS, "timeout": TIMEOUT}
    if servers:
        body["mcp_servers"] = servers
    if session:
        body["resume"] = session
    return body


def run(body):
    """One run on claude-runner; raises requests.HTTPError when every route failed."""
    r = requests.post(f"{config.CLAUDE_RUNNER_URL}/v1/run", json=body, timeout=TIMEOUT + 30,
                      headers={"Authorization": f"Bearer {config.CLAUDE_RUNNER_TOKEN}"})
    r.raise_for_status()
    return r.json()


def ask(question, device):
    """Ask Claude Code headless; follow-ups from the same Echo continue with resume."""
    session = recall(("claude", device))
    try:
        data = run(run_request(question, session))
    except requests.HTTPError:
        if not session:
            raise
        # Session is gone (runner rebuilt without its home): ask again without context.
        data = run(run_request(question, None))
    remember(("claude", device), data["session_id"])
    return data["result"]
