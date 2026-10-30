"""Self-check: request checks, routing by skill id, tools, late answers through Home Assistant.
python test_app.py"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

os.environ["SKILL_JARVIS"] = "amzn1.ask.skill.jarvis"
os.environ["SKILL_CLAUDE"] = "amzn1.ask.skill.claude"
os.environ["ANSWER_DEADLINE"] = "0.2"

import importlib  # noqa: E402

from fastapi import HTTPException  # noqa: E402

import app  # noqa: E402

# --- an empty skill id in .env must not enable a skill
os.environ["SKILL_JARVIS"] = ""
assert "" not in importlib.reload(app).SKILLS
os.environ["SKILL_JARVIS"] = "amzn1.ask.skill.jarvis"
importlib.reload(app)

app.verifier.verify = lambda headers, body, env: None
spoken = []
app.announce = spoken.append


def request(skill, intent=None, query=None, new=True, age=0):
    """Build a skill request in the format Amazon sends."""
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age)).strftime("%Y-%m-%dT%H:%M:%SZ")
    req = {"type": "LaunchRequest", "timestamp": ts}
    if intent:
        req = {"type": "IntentRequest", "timestamp": ts,
               "intent": {"name": intent, "slots": {"query": {"name": "query", "value": query}}}}
    env = {"version": "1.0", "session": {"new": new},
           "context": {"System": {"application": {"applicationId": skill}, "device": {"deviceId": "echo-1"}}},
           "request": req}
    return app.handle({}, json.dumps(env).encode())


def speech(resp):
    """Spoken text of a skill response."""
    return resp["response"]["outputSpeech"]["text"]


def rejected(status, **kw):
    """True if the request is rejected with exactly this HTTP status."""
    try:
        request(**kw)
    except HTTPException as e:
        return e.status_code == status
    return False


# --- foreign skills and stale requests do not get through
assert rejected(403, skill="amzn1.ask.skill.other")
assert rejected(400, skill="amzn1.ask.skill.jarvis", age=200)
try:
    app.handle({}, b"{}")
    raise AssertionError("empty request must be rejected")
except HTTPException as e:
    assert e.status_code == 400


class FakeResponse:
    """LiteLLM response with a fixed message."""

    def __init__(self, message):
        self.message = message

    def raise_for_status(self):
        """LiteLLM always succeeds."""

    def json(self):
        """Chat completion in OpenAI format."""
        return {"choices": [{"message": self.message}]}


# --- Claude gets Home Assistant read-only
app.HA_TOKEN = "t"
calls = []
app.subprocess.run = lambda cmd, **kw: calls.append(cmd) or type(
    "Done", (), {"returncode": 0, "stdout": '{"session_id": "s", "result": "ok"}', "stderr": ""})()
assert app.ask_claude("Wie hoch war die letzte Stromrechnung?", "echo-ha") == "ok"
allowed = calls[0][calls[0].index("--allowedTools") + 1].split(",")
assert "mcp__ha__homeassistant__GetLiveContext" in allowed
assert not [t for t in allowed if "Hass" in t or t == "mcp__ha"], allowed
assert '"ha"' in calls[0][calls[0].index("--mcp-config") + 1]

# --- Jarvis calls tools, the result goes back to the model but not into the history
sent = []
replies = [
    {"content": None, "tool_calls": [{"id": "c1", "type": "function",
                                      "function": {"name": "house_state", "arguments": "{}"}}]},
    {"content": "Der Abschlag ist am 1. November fällig."},
]
app.TOOLS["house_state"] = lambda: "Abschlag fällig am: 2026-11-01"
app.requests.post = lambda url, json=None, **kw: sent.append(json["messages"]) or FakeResponse(replies.pop(0))
assert app.ask_jarvis("Wann ist der Abschlag fällig?", "echo-x") == "Der Abschlag ist am 1. November fällig."
assert sent[1][-1] == {"role": "tool", "tool_call_id": "c1", "content": "Abschlag fällig am: 2026-11-01"}
assert [m["role"] for m in app.history[("jarvis", "echo-x")][1]] == ["system", "user", "assistant"]


# --- MCP answers as SSE without charset: umlauts must survive
class FakeSse:
    """SSE response of an MCP server, content type without charset."""

    headers = {}
    content = 'event: message\ndata: {"result": {"content": [{"text": "Küche"}]}}'.encode("utf-8")
    text = content.decode("latin-1")

    def raise_for_status(self):
        """Server always answers 200."""


app.requests.post = lambda *a, **kw: FakeSse()
assert app.mcp_call("http://x/mcp", "t", "tool", {}) == "Küche"


# --- waste calendar: only the next 21 days, sorted, across the turn of the year
def ms(day):
    """Date in the OData format of the waste calendar API."""
    return f"/Date({int(datetime(day.year, day.month, day.day, 12).timestamp() * 1000)})/"


D = datetime.fromisoformat
years = []
items = {2026: [{"Termin": ms(D("2026-12-20")), "Name": "Restmüll"}, {"Termin": ms(D("2026-12-29")), "Name": "Biotonne"},
                {"Termin": ms(D("2026-12-01")), "Name": "Papier"}],
         2027: [{"Termin": ms(D("2027-01-05")), "Name": "Papier"}, {"Termin": ms(D("2027-02-01")), "Name": "Glas"}]}
app.requests.get = lambda url, params=None, **kw: years.append(params["jahr"]) or type(
    "R", (), {"raise_for_status": lambda self: None, "json": lambda self: {"d": items[params["jahr"]]}})()
cal = app.waste_calendar(D("2026-12-20").date())
assert years == [2026, 2027], years
assert cal.splitlines()[1:] == ["Sonntag, 20.12.2026: Restmüll", "Dienstag, 29.12.2026: Biotonne",
                                "Dienstag, 05.01.2027: Papier"], cal

# --- a broken tool does not break the answer
app.TOOLS["web_search"] = lambda query: 1 / 0
assert "nicht erreichbar" in app.run_tool({"function": {"name": "web_search", "arguments": '{"query": "x"}'}})

# --- decimal point is spoken, dates and versions stay
assert app.speakable("minus 1.12 Euro am 01.11.2026, Version 2.11.4") == \
    "minus 1 Komma 12 Euro am 01.11.2026, Version 2.11.4"
assert app.speakable("Insgesamt 166,00 Euro, Gas 95.00 Euro.") == "Insgesamt 166 Euro, Gas 95 Euro."

# --- skill id picks the brain, decimal comma is spoken
app.BRAINS["jarvis"] = lambda q, d: f"jarvis: {q} 1,6"
app.BRAINS["claude"] = lambda q, d: f"claude: {q}"
r = request("amzn1.ask.skill.jarvis", "QueryIntent", "wie spät ist es")
assert speech(r) == "jarvis: wie spät ist es 1 Komma 6", speech(r)
assert r["response"]["shouldEndSession"] is True
assert speech(request("amzn1.ask.skill.claude", "QueryIntent", "x", new=False)) == "claude: x"

# --- after "oeffne Jarvis" the microphone stays open
assert request("amzn1.ask.skill.jarvis")["response"]["shouldEndSession"] is False
assert request("amzn1.ask.skill.jarvis", "QueryIntent", "x", new=False)["response"]["shouldEndSession"] is False

# --- too slow: "Moment" first, then the announcement through Home Assistant
app.BRAINS["claude"] = lambda q, d: time.sleep(0.5) or "**spaete** Antwort"
r = request("amzn1.ask.skill.claude", "QueryIntent", "denk nach")
assert speech(r).startswith("Moment") and r["response"]["shouldEndSession"] is True
for _ in range(50):
    if spoken:
        break
    time.sleep(0.1)
assert spoken == ["spaete Antwort"], spoken

print("ok")
