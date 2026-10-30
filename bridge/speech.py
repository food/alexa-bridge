"""Text Alexa can speak, and late answers spoken through Home Assistant."""
import logging
import re
import time

import requests

from . import config

log = logging.getLogger("uvicorn.error")

LAST_CALLED = (
    "{{ states.media_player | selectattr('attributes.last_called','defined')"
    " | selectattr('attributes.last_called') | map(attribute='object_id')"
    " | list | first | default('" + config.HA_FALLBACK_DEVICE + "') }}"
)
FAILED = "Das hat leider nicht geklappt."


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
    headers = {"Authorization": f"Bearer {config.HA_TOKEN}"}
    target = requests.post(f"{config.HA_URL}/api/template", headers=headers,
                           json={"template": LAST_CALLED}, timeout=10).text.strip()
    if not re.fullmatch(r"\w+", target):
        target = config.HA_FALLBACK_DEVICE
    requests.post(f"{config.HA_URL}/api/services/notify/alexa_media_{target}", headers=headers,
                  json={"message": text, "data": {"type": "tts"}}, timeout=10).raise_for_status()


def deliver(future, since):
    """Callback for answers that missed the skill time window."""
    try:
        text = speakable(future.result())
    except Exception:
        log.exception("brain failed")
        text = FAILED
    # While Alexa is still saying "Moment" she swallows the announcement.
    time.sleep(max(0, since + 3 - time.time()))
    try:
        announce(text)
    except Exception:
        log.exception("announcement through Home Assistant failed")
