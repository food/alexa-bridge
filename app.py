"""Endpoint for two custom Alexa skills: "Jarvis" asks LiteLLM, "Schlau" asks the Claude Code CLI.

Jarvis gets its own tools (memory, Home Assistant state, waste calendar, SearXNG web search)
and usually stays below the skill timeout.

The skill id decides which brain answers. If the brain takes longer than ANSWER_DEADLINE,
Alexa says "Moment" and the answer is spoken later through Home Assistant
(notify.alexa_media_*) on the Echo that was addressed last.

Everything Alexa speaks and everything the models read (prompts, tool descriptions) stays
German: the prompt language drives the answer language.
"""
import json
import logging
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timedelta, timezone

import requests
from ask_sdk_webservice_support.verifier import RequestVerifier, VerificationException
from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:4000")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "chat")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "sonnet")
MEMORY_URL = os.environ.get("MEMORY_URL", "http://localhost:8765/mcp")
MEMORY_TOKEN = os.environ.get("MEMORY_TOKEN", "")
HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
# media_player object id that answers when HA cannot tell which Echo was addressed last.
HA_FALLBACK_DEVICE = os.environ.get("HA_FALLBACK_DEVICE", "echo")
# Topics the house_state tool advertises to the model; list what your HA actually exposes.
HOUSE_TOPICS = os.environ.get("HOUSE_TOPICS", "Strom, Gas, Wasser, Rechnungen, Zaehler, Solaranlage, Geraete und Raeume")
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8888")
# Pickup dates from a c-trace OData endpoint of a municipality app; everything comes from .env.
CTRACE_URL = os.environ.get("CTRACE_URL", "")
CTRACE_USER = os.environ.get("CTRACE_USER", "")
CTRACE_PASSWORD = os.environ.get("CTRACE_PASSWORD", "")
WASTE_TOWN = os.environ.get("WASTE_TOWN", "")
WASTE_STREET = os.environ.get("WASTE_STREET", "")
WASTE_HOUSE_NO = os.environ.get("WASTE_HOUSE_NO", "")
ANSWER_DEADLINE = float(os.environ.get("ANSWER_DEADLINE", "6"))
SKILLS = {skill: brain for skill, brain in ((os.environ.get("SKILL_JARVIS"), "jarvis"),
                                           (os.environ.get("SKILL_CLAUDE"), "claude")) if skill}

# Amazon allows at most 150 s between request timestamp and local clock.
MAX_CLOCK_SKEW = 150
# Conversation history lives in memory only, a restart forgets it.
HISTORY_TTL = 300
LAST_CALLED = (
    "{{ states.media_player | selectattr('attributes.last_called','defined')"
    " | selectattr('attributes.last_called') | map(attribute='object_id')"
    " | list | first | default('" + HA_FALLBACK_DEVICE + "') }}"
)

log = logging.getLogger("uvicorn.error")
app = FastAPI()
verifier = RequestVerifier()
history = {}  # (brain, deviceId) -> (timestamp, messages or session_id)
# Last messages sent along to LiteLLM (plus the system prompt).
HISTORY_MESSAGES = 10
# Home Assistant read-only: the switching tools of the HA MCP server stay blocked,
# Alexa keeps doing that herself.
HA_TOOLS = ["mcp__ha__homeassistant__GetLiveContext", "mcp__ha__todo__get_items"]
# Without this hint Haiku looked for house values in memory instead of Home Assistant.
CLAUDE_HINT = (
    " Werte aus dem Haus (Strom, Gas, Wasser, Rechnungen, Abschlaege, Zaehler, Solaranlage, Geraete)"
    " holst du mit GetLiveContext aus Home Assistant. Wissen ueber den Nutzer, seine Projekte und"
    " seine Infrastruktur steht im Gedaechtnis (memory). Aktuelles aus der Welt per Websuche."
)
# Without this hint the lane often guesses on "meine Spielesammlung" instead of asking memory.
JARVIS_HINT = (
    " Rate nicht, sondern nutze die Werkzeuge: Fragen zum Nutzer, seinen Projekten, Apps und Servern"
    " (\"mein\", \"unser\") mit memory_query, Werte aus dem Haus mit house_state, Muellabfuhr mit"
    " waste_calendar, Aktuelles mit web_search."
    " Sag erst dann, dass du etwas nicht weisst, wenn das passende Werkzeug nichts geliefert hat."
)
# Upper bound of model rounds per question, in case the lane keeps calling tools.
MAX_TOOL_ROUNDS = 4
JARVIS_TOOLS = [
    {"type": "function", "function": {
        "name": "memory_query",
        "description": "Persoenliches Gedaechtnis des Nutzers: Projekte, Infrastruktur, Vorlieben, Personen.",
        "parameters": {"type": "object", "required": ["question"], "properties": {"question": {
            "type": "string",
            # "des Nutzers" instead of "meine" makes the librarian answer about the person, not the topic.
            "description": "Die Frage in der Ich-Form, wie der Nutzer sie gestellt hat, z. B. 'Wie heisst meine ...?'"}}}}},
    {"type": "function", "function": {
        "name": "house_state",
        "description": f"Aktuelle Werte aus Home Assistant: {HOUSE_TOPICS}.",
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


def system_prompt():
    """Rules for spoken answers, including the current date and time."""
    return (
        f"Heute ist der {datetime.now():%d.%m.%Y}, es ist {datetime.now():%H:%M} Uhr. "
        "Du antwortest ueber einen Alexa-Lautsprecher: auf Deutsch, in hoechstens drei kurzen Saetzen, "
        "ohne Aufzaehlungen, Tabellen, Markdown oder Emojis. Zahlen als Ziffern, "
        "Dezimalzahlen mit Komma."
    )


def recall(key):
    """Return the conversation state of this device while it is younger than HISTORY_TTL."""
    at, state = history.get(key, (0, None))
    return state if time.time() - at < HISTORY_TTL else None


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
    """Tool: ask the shared memory (librarian)."""
    return mcp_call(MEMORY_URL, MEMORY_TOKEN, "memory_query", {"question": question})


def house_state():
    """Tool: live context from Home Assistant, only entities exposed to Assist."""
    return mcp_call(f"{HA_URL}/api/mcp", HA_TOKEN, "homeassistant__GetLiveContext", {})


def web_search(query):
    """Tool: titles and snippets of the first SearXNG results."""
    r = requests.get(f"{SEARXNG_URL}/search", params={"q": query, "format": "json"}, timeout=20)
    r.raise_for_status()
    return "\n".join(f"{x['title']}: {x.get('content', '')}" for x in r.json()["results"][:6])


WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def waste_calendar(today=None):
    """Tool: pickup dates of the next 21 days, one line per pickup."""
    today = today or datetime.now().date()
    pickups = []
    for year in sorted({today.year, (today + timedelta(days=21)).year}):
        r = requests.get(CTRACE_URL, auth=(CTRACE_USER, CTRACE_PASSWORD), timeout=20,
                         headers={"Accept": "application/json"},
                         params={"ort": f"'{WASTE_TOWN}'", "ortsteil": "''", "strasse": f"'{WASTE_STREET}'",
                                 "hausNr": f"'{WASTE_HOUSE_NO}'", "hausNrZus": "''", "jahr": year})
        r.raise_for_status()
        for item in r.json()["d"]:
            # OData sends "/Date(1793232000000)/", milliseconds since 1970.
            day = datetime.fromtimestamp(int(re.sub(r"\D", "", item["Termin"])) / 1000).date()
            if today <= day <= today + timedelta(days=21):
                pickups.append((day, item["Name"]))
    lines = [f"{WEEKDAYS[d.weekday()]}, {d:%d.%m.%Y}: {name}" for d, name in sorted(pickups)]
    return f"Heute ist {WEEKDAYS[today.weekday()]}, {today:%d.%m.%Y}.\n" + ("\n".join(lines) or "Keine Abfuhr.")


TOOLS = {"memory_query": memory_query, "house_state": house_state, "waste_calendar": waste_calendar,
         "web_search": web_search}


def run_tool(call):
    """Run a tool call of the lane; errors go back to the model as text."""
    name = call["function"]["name"]
    try:
        return TOOLS[name](**json.loads(call["function"]["arguments"] or "{}"))
    except Exception as e:
        log.warning("tool %s failed: %s", name, e)
        return f"Werkzeug {name} nicht erreichbar: {e}"


def ask_jarvis(question, device):
    """Ask the LiteLLM lane with tools and keep the history per Echo.

    Only question and answer go into the history, not the tool results: the house state
    alone is about 20 kB and would slow down every follow-up question.
    """
    messages = recall(("jarvis", device)) or [{"role": "system", "content": system_prompt() + JARVIS_HINT}]
    messages = messages[:1] + messages[1:][-HISTORY_MESSAGES:] + [{"role": "user", "content": question}]
    turn = list(messages)
    answer = ""
    for _ in range(MAX_TOOL_ROUNDS):
        r = requests.post(f"{LITELLM_URL}/v1/chat/completions", timeout=120,
                          json={"model": LITELLM_MODEL, "messages": turn, "tools": JARVIS_TOOLS})
        r.raise_for_status()
        message = r.json()["choices"][0]["message"]
        if not message.get("tool_calls"):
            answer = message.get("content") or ""
            break
        turn.append(message)
        for call in message["tool_calls"]:
            log.info("jarvis uses %s", call["function"]["name"])
            turn.append({"role": "tool", "tool_call_id": call["id"], "content": run_tool(call)})
    history[("jarvis", device)] = (time.time(), messages + [{"role": "assistant", "content": answer}])
    return answer


def ask_claude(question, device):
    """Ask Claude Code headless; follow-ups from the same Echo continue with --resume."""
    servers = {}
    if MEMORY_TOKEN:
        servers["memory"] = {"type": "http", "url": MEMORY_URL, "headers": {"Authorization": f"Bearer {MEMORY_TOKEN}"}}
    if HA_TOKEN:
        servers["ha"] = {"type": "http", "url": f"{HA_URL}/api/mcp", "headers": {"Authorization": f"Bearer {HA_TOKEN}"}}
    cmd = ["claude", "-p", "--output-format", "json", "--model", CLAUDE_MODEL,
           "--append-system-prompt", system_prompt() + CLAUDE_HINT,
           "--allowedTools", ",".join(["WebSearch", "WebFetch", "mcp__memory"] + HA_TOOLS)]
    if servers:
        cmd += ["--mcp-config", json.dumps({"mcpServers": servers})]
    session = recall(("claude", device))
    out = subprocess.run(cmd + (["--resume", session] if session else []), input=question,
                         capture_output=True, text=True, encoding="utf-8", timeout=300)
    if out.returncode and session:
        # Session is gone (container recreated): ask again without context.
        out = subprocess.run(cmd, input=question, capture_output=True, text=True, encoding="utf-8", timeout=300)
    if out.returncode:
        raise RuntimeError(f"claude exit {out.returncode}: {(out.stderr or out.stdout)[-500:]}")
    data = json.loads(out.stdout)
    history[("claude", device)] = (time.time(), data["session_id"])
    return data["result"]


BRAINS = {"jarvis": ask_jarvis, "claude": ask_claude}
# One pool per brain, so long Claude runs do not block Jarvis.
pools = {brain: ThreadPoolExecutor(max_workers=4) for brain in BRAINS}


def speakable(text):
    """Strip markdown leftovers and spell out decimals Alexa would otherwise read as money."""
    text = re.sub(r"[*_#`|>]", "", text or "")
    text = re.sub(r"(\d)[.,]00(?![\d.])", r"\1", text)
    text = re.sub(r"(\d),(\d)", r"\1 Komma \2", text)
    # A decimal point ("1.12 Euro") is read as "eins Punkt zwoelf"; dates like 01.11.2026 and
    # version numbers stay untouched thanks to the lookarounds.
    text = re.sub(r"(?<![\d.])(\d+)\.(\d{1,2})(?![\d.])", r"\1 Komma \2", text)
    return text.strip() or "Dazu fällt mir nichts ein."


def announce(text):
    """Speak the text on the Echo that alexa_media marks as last called."""
    headers = {"Authorization": f"Bearer {HA_TOKEN}"}
    target = requests.post(f"{HA_URL}/api/template", headers=headers,
                           json={"template": LAST_CALLED}, timeout=10).text.strip()
    if not re.fullmatch(r"\w+", target):
        target = HA_FALLBACK_DEVICE
    requests.post(f"{HA_URL}/api/services/notify/alexa_media_{target}", headers=headers,
                  json={"message": text, "data": {"type": "tts"}}, timeout=10).raise_for_status()


def deliver(future, since):
    """Callback for answers that missed the skill time window."""
    try:
        text = speakable(future.result())
    except Exception:
        log.exception("brain failed")
        text = "Das hat leider nicht geklappt."
    # While Alexa is still saying "Moment" she swallows the announcement.
    time.sleep(max(0, since + 3 - time.time()))
    try:
        announce(text)
    except Exception:
        log.exception("announcement through Home Assistant failed")


def reply(text, end):
    """Build the Alexa response; end=False keeps the microphone open for the next question."""
    return {"version": "1.0",
            "response": {"outputSpeech": {"type": "PlainText", "text": text}, "shouldEndSession": end}}


def check(headers, body, env):
    """Check timestamp, skill id and Amazon signature; cheap checks first."""
    ts = datetime.fromisoformat(env["request"]["timestamp"].replace("Z", "+00:00"))
    if abs((datetime.now(timezone.utc) - ts).total_seconds()) > MAX_CLOCK_SKEW:
        raise HTTPException(400, "stale request")
    brain = SKILLS.get(env["context"]["System"]["application"]["applicationId"])
    if not brain:
        raise HTTPException(403, "unknown skill")
    try:
        verifier.verify(headers, body.decode("utf-8"), None)
    except VerificationException as e:
        raise HTTPException(400, f"signature: {e}")
    return brain


def handle(headers, body):
    """Process a skill request synchronously (runs in the thread pool)."""
    try:
        env = json.loads(body)
        brain = check(headers, body, env)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(400, "malformed request")
    req = env["request"]
    if req["type"] == "LaunchRequest":
        return reply("Ja?", False)
    if req["type"] != "IntentRequest":
        return {"version": "1.0", "response": {}}

    # A one-shot question ("frag Jarvis, ...") ends the session, after "oeffne Jarvis" it stays open.
    end = env["session"]["new"]
    intent = req["intent"]
    if intent["name"] in ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NavigateHomeIntent"):
        return reply("Bis dann.", True)
    if intent["name"] == "AMAZON.HelpIntent":
        return reply("Stell mir einfach eine Frage.", False)
    question = (intent.get("slots", {}).get("query", {}).get("value") or "").strip()
    if intent["name"] != "QueryIntent" or not question:
        return reply("Das habe ich nicht verstanden.", end)

    device = env["context"]["System"]["device"]["deviceId"]
    log.info("%s asked: %s", brain, question)
    start = time.time()
    future = pools[brain].submit(BRAINS[brain], question, device)
    try:
        answer = future.result(timeout=ANSWER_DEADLINE)
        log.info("%s answered after %.1f s", brain, time.time() - start)
        return reply(speakable(answer), end)
    except TimeoutError:
        since = time.time()
        future.add_done_callback(lambda f: log.info("%s answered after %.1f s, delivered later", brain,
                                                    time.time() - start))
        future.add_done_callback(lambda f: deliver(f, since))
        return reply("Moment, ich denke nach.", True)
    except Exception:
        log.exception("brain failed")
        return reply("Das hat leider nicht geklappt.", True)


@app.post("/alexa")
async def alexa(request: Request):
    """Skill endpoint as entered in the Amazon developer console."""
    return await run_in_threadpool(handle, dict(request.headers), await request.body())


@app.get("/health")
def health():
    """Liveness check for Traefik and Docker."""
    return {"ok": True, "skills": sorted(SKILLS.values())}
