"""Alexa skill protocol: request checks, routing by skill id and the answer time window.

The skill id decides which brain answers. If the brain takes longer than ANSWER_DEADLINE,
Alexa says "Moment" and the answer is spoken later through Home Assistant on the Echo that
was addressed last.
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone

from ask_sdk_webservice_support.verifier import RequestVerifier, VerificationException
from fastapi import HTTPException

from . import claude, config, jarvis, speech

log = logging.getLogger("uvicorn.error")
verifier = RequestVerifier()
BRAINS = {"jarvis": jarvis.ask, "claude": claude.ask}
# One pool per brain, so long Claude runs do not block Jarvis.
pools = {brain: ThreadPoolExecutor(max_workers=4) for brain in BRAINS}


def reply(text, end):
    """Build the Alexa response; end=False keeps the microphone open for the next question."""
    return {"version": "1.0",
            "response": {"outputSpeech": {"type": "PlainText", "text": text}, "shouldEndSession": end}}


def check(headers, body, env):
    """Check timestamp, skill id and Amazon signature; cheap checks first."""
    ts = datetime.fromisoformat(env["request"]["timestamp"].replace("Z", "+00:00"))
    if abs((datetime.now(timezone.utc) - ts).total_seconds()) > config.MAX_CLOCK_SKEW:
        raise HTTPException(400, "stale request")
    brain = config.SKILLS.get(env["context"]["System"]["application"]["applicationId"])
    if not brain:
        raise HTTPException(403, "unknown skill")
    try:
        verifier.verify(headers, body.decode("utf-8"), None)
    except VerificationException as e:
        raise HTTPException(400, f"signature: {e}")
    return brain


def answer(brain, question, device, end):
    """Let the brain answer within ANSWER_DEADLINE, otherwise announce and deliver later."""
    log.info("%s asked: %s", brain, question)
    start = time.time()
    future = pools[brain].submit(BRAINS[brain], question, device)
    try:
        text = future.result(timeout=config.ANSWER_DEADLINE)
        log.info("%s answered after %.1f s", brain, time.time() - start)
        return reply(speech.speakable(text), end)
    except TimeoutError:
        since = time.time()
        future.add_done_callback(lambda f: log.info("%s answered after %.1f s, delivered later", brain,
                                                    time.time() - start))
        future.add_done_callback(lambda f: speech.deliver(f, since))
        return reply("Moment, ich denke nach.", True)
    except Exception:
        log.exception("brain failed")
        return reply(speech.FAILED, True)


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
    return answer(brain, question, env["context"]["System"]["device"]["deviceId"], end)
