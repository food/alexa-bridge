"""Per-Echo conversation state and the shared system prompt.

The prompt stays German: the prompt language drives the answer language.
"""
import time
from datetime import datetime

from . import config

history = {}  # (brain, deviceId) -> (timestamp, messages or session_id)


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
    return state if time.time() - at < config.HISTORY_TTL else None


def remember(key, state):
    """Store the conversation state of this device."""
    history[key] = (time.time(), state)
