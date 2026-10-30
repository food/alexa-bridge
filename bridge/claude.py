"""Schlau: headless Claude Code CLI with web search, memory and read-only Home Assistant."""
import json
import subprocess

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


def command():
    """claude -p invocation with MCP servers and the allowed tools."""
    servers = {}
    if config.MEMORY_TOKEN:
        servers["memory"] = {"type": "http", "url": config.MEMORY_URL,
                             "headers": {"Authorization": f"Bearer {config.MEMORY_TOKEN}"}}
    if config.HA_TOKEN:
        servers["ha"] = {"type": "http", "url": f"{config.HA_URL}/api/mcp",
                         "headers": {"Authorization": f"Bearer {config.HA_TOKEN}"}}
    cmd = ["claude", "-p", "--output-format", "json", "--model", config.CLAUDE_MODEL,
           "--append-system-prompt", system_prompt() + HINT,
           "--allowedTools", ",".join(["WebSearch", "WebFetch", "mcp__memory"] + HA_TOOLS)]
    if servers:
        cmd += ["--mcp-config", json.dumps({"mcpServers": servers})]
    return cmd


def ask(question, device):
    """Ask Claude Code headless; follow-ups from the same Echo continue with --resume."""
    cmd = command()
    session = recall(("claude", device))
    out = subprocess.run(cmd + (["--resume", session] if session else []), input=question,
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if out.returncode and session:
        # Session is gone (container recreated): ask again without context.
        out = subprocess.run(cmd, input=question, capture_output=True, text=True, encoding="utf-8", timeout=300)
    if out.returncode:
        raise RuntimeError(f"claude exit {out.returncode}: {(out.stderr or out.stdout)[-500:]}")
    data = json.loads(out.stdout)
    remember(("claude", device), data["session_id"])
    return data["result"]
