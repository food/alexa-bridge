"""Jarvis: LiteLLM lane with tool calling."""
import logging

import requests

from . import config, tools
from .conversation import recall, remember, system_prompt

log = logging.getLogger("uvicorn.error")

# Without this hint the lane often guesses on "meine Spielesammlung" instead of asking memory.
HINT = (
    " Rate nicht, sondern nutze die Werkzeuge: Fragen zum Nutzer, seinen Projekten, Apps und Servern"
    " (\"mein\", \"unser\") mit memory_query, Werte aus dem Haus mit house_state, Muellabfuhr mit"
    " waste_calendar, Aktuelles mit web_search."
    " Sag erst dann, dass du etwas nicht weisst, wenn das passende Werkzeug nichts geliefert hat."
)


def ask(question, device):
    """Ask the LiteLLM lane with tools and keep the history per Echo.

    Only question and answer go into the history, not the tool results: the house state
    alone is about 20 kB and would slow down every follow-up question.
    """
    messages = recall(("jarvis", device)) or [{"role": "system", "content": system_prompt() + HINT}]
    messages = messages[:1] + messages[1:][-config.HISTORY_MESSAGES:] + [{"role": "user", "content": question}]
    turn = list(messages)
    answer = ""
    for _ in range(config.MAX_TOOL_ROUNDS):
        r = requests.post(f"{config.LITELLM_URL}/v1/chat/completions", timeout=120,
                          json={"model": config.LITELLM_MODEL, "messages": turn, "tools": tools.SCHEMA})
        r.raise_for_status()
        message = r.json()["choices"][0]["message"]
        if not message.get("tool_calls"):
            answer = message.get("content") or ""
            break
        turn.append(message)
        for call in message["tool_calls"]:
            log.info("jarvis uses %s", call["function"]["name"])
            turn.append({"role": "tool", "tool_call_id": call["id"], "content": tools.run(call)})
    remember(("jarvis", device), messages + [{"role": "assistant", "content": answer}])
    return answer
