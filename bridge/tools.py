"""Tools for the Jarvis lane: memory, Home Assistant state, waste calendar and web search.

Tool descriptions and tool output stay German, the model reads them.
"""
import json
import logging
import re
from datetime import datetime, timedelta

import requests

from . import config

log = logging.getLogger("uvicorn.error")

SCHEMA = [
    {"type": "function", "function": {
        "name": "memory_query",
        "description": "Persoenliches Gedaechtnis des Nutzers: Projekte, Infrastruktur, Vorlieben, Personen.",
        "parameters": {"type": "object", "required": ["question"], "properties": {"question": {
            "type": "string",
            # "des Nutzers" instead of "meine" makes the librarian answer about the person, not the topic.
            "description": "Die Frage in der Ich-Form, wie der Nutzer sie gestellt hat, z. B. 'Wie heisst meine ...?'"}}}}},
    {"type": "function", "function": {
        "name": "house_state",
        "description": f"Aktuelle Werte aus Home Assistant: {config.HOUSE_TOPICS}.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "waste_calendar",
        "description": "Muellabfuhr der naechsten drei Wochen: welche Tonne an welchem Tag abgeholt wird.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Websuche fuer Aktuelles: Oeffnungszeiten, Wetter, Nachrichten, Preise.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
]

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def mcp_call(url, token, tool, arguments):
    """Call a tool on an MCP server (streamable HTTP) and return its text."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    init = {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "alexa-bridge", "version": "1"}}
    r = requests.post(url, headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": init},
                      timeout=30)
    r.raise_for_status()
    if r.headers.get("mcp-session-id"):
        headers["mcp-session-id"] = r.headers["mcp-session-id"]
    requests.post(url, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=30)
    r = requests.post(url, headers=headers, timeout=60, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}})
    r.raise_for_status()
    # Depending on the server the answer is plain JSON or an SSE line "data: {...}". SSE comes
    # without a charset, so requests guesses Latin-1 and breaks the umlauts.
    text = r.content.decode("utf-8")
    body = json.loads(text[text.index("{"):])
    return body["result"]["content"][0]["text"]


def memory_query(question):
    """Ask the shared memory (librarian)."""
    return mcp_call(config.MEMORY_URL, config.MEMORY_TOKEN, "memory_query", {"question": question})


def house_state():
    """Live context from Home Assistant, only entities exposed to Assist."""
    return mcp_call(f"{config.HA_URL}/api/mcp", config.HA_TOKEN, "homeassistant__GetLiveContext", {})


def waste_calendar(today=None):
    """Pickup dates of the next 21 days, one line per pickup."""
    today = today or datetime.now().date()
    pickups = []
    for year in sorted({today.year, (today + timedelta(days=21)).year}):
        r = requests.get(config.CTRACE_URL, auth=(config.CTRACE_USER, config.CTRACE_PASSWORD), timeout=20,
                         headers={"Accept": "application/json"},
                         params={"ort": f"'{config.WASTE_TOWN}'", "ortsteil": "''",
                                 "strasse": f"'{config.WASTE_STREET}'", "hausNr": f"'{config.WASTE_HOUSE_NO}'",
                                 "hausNrZus": "''", "jahr": year})
        r.raise_for_status()
        for item in r.json()["d"]:
            # OData sends "/Date(1793232000000)/", milliseconds since 1970.
            day = datetime.fromtimestamp(int(re.sub(r"\D", "", item["Termin"])) / 1000).date()
            if today <= day <= today + timedelta(days=21):
                pickups.append((day, item["Name"]))
    lines = [f"{WEEKDAYS[d.weekday()]}, {d:%d.%m.%Y}: {name}" for d, name in sorted(pickups)]
    return f"Heute ist {WEEKDAYS[today.weekday()]}, {today:%d.%m.%Y}.\n" + ("\n".join(lines) or "Keine Abfuhr.")


def web_search(query):
    """Titles and snippets of the first SearXNG results."""
    r = requests.get(f"{config.SEARXNG_URL}/search", params={"q": query, "format": "json"}, timeout=20)
    r.raise_for_status()
    return "\n".join(f"{x['title']}: {x.get('content', '')}" for x in r.json()["results"][:6])


TOOLS = {"memory_query": memory_query, "house_state": house_state, "waste_calendar": waste_calendar,
         "web_search": web_search}


def run(call):
    """Run a tool call of the lane; errors go back to the model as text."""
    name = call["function"]["name"]
    try:
        return TOOLS[name](**json.loads(call["function"]["arguments"] or "{}"))
    except Exception as e:
        log.warning("tool %s failed: %s", name, e)
        return f"Werkzeug {name} nicht erreichbar: {e}"
