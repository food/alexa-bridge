"""Settings from the environment; everything specific to one home lives in .env."""
import os

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
# Last messages sent along to LiteLLM (plus the system prompt).
HISTORY_MESSAGES = 10
# Upper bound of model rounds per question, in case the lane keeps calling tools.
MAX_TOOL_ROUNDS = 4
