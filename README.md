# alexa-bridge

Endpoint for two custom Alexa skills that pass free-form questions to your own models:

- **"Alexa, frag Jarvis ..."** -> an OpenAI-compatible LiteLLM lane with tool calling and
  its own tools: `memory_query` (an MCP memory server), `house_state` (`GetLiveContext` from
  Home Assistant's MCP server), `waste_calendar` (pickup dates of the next 21 days from a
  c-trace municipality API) and `web_search` (snippets of the first SearXNG results).
  Usually answers in 1.5-3 s.
- **"Alexa, frag Schlau ..."** -> headless Claude Code CLI with WebSearch, the memory MCP and
  read-only Home Assistant tools (`GetLiveContext`, `todo get_items`). The invocation name is
  not "Claude" because German speech recognition hears "cloud".

Jarvis is the fast path for everyday questions, Schlau the thorough one (10-20 s, the answer
is delivered later).

"Alexa, oeffne Jarvis" keeps the microphone open, after that no prefix is needed.
Follow-up questions from the same Echo keep their context for 5 minutes.

A skill has to answer within about 8 s. If the model is not done after `ANSWER_DEADLINE`
(6 s), Alexa says "Moment, ich denke nach" and the answer is spoken through Home Assistant
(`notify.alexa_media_<device>`, needs the alexa_media_player integration) on the Echo whose
`media_player` currently has `last_called`.

Every request is checked: timestamp (+-150 s), skill id and Amazon signature
(`Signature-256`, certificate chain). The endpoint is public, this is what keeps it closed.

Everything Alexa speaks and everything the models read (prompts, tool descriptions,
`skill-model.json`) is German on purpose: the prompt language drives the answer language.

## Running

```bash
cp .env.example .env    # fill in URLs, tokens and skill ids
docker compose up -d --build
```

The container listens on port 8000 (published as 8101). Amazon only talks HTTPS on port 443,
so put a reverse proxy with a trusted certificate in front and forward only the path `/alexa`.

Home Assistant needs the "Model Context Protocol Server" integration (Assist API). It only
shows entities that are exposed to Assist; expose the sensors you want to ask about and list
their topics in `HOUSE_TOPICS`.

## Layout

| Module | Purpose |
|---|---|
| `bridge/app.py` | FastAPI routes `/alexa` and `/health` |
| `bridge/config.py` | settings from the environment |
| `bridge/skill.py` | Alexa protocol: request checks, routing by skill id, answer time window |
| `bridge/conversation.py` | per-Echo history and the system prompt |
| `bridge/tools.py` | MCP client and the Jarvis tools |
| `bridge/jarvis.py` | LiteLLM tool-calling loop |
| `bridge/claude.py` | Claude Code CLI call |
| `bridge/speech.py` | speakable text and late answers through Home Assistant |

## Creating the skills (Alexa Developer Console)

Once per skill:

1. *Create Skill* -> language German (DE), model *Custom*, hosting *Provision your own*.
2. *Interaction Model* -> *JSON Editor* -> paste `skill-model.json`. For the Claude skill
   change `invocationName` to `schlau`. *Save* and *Build*.
3. *Endpoint* -> HTTPS -> `https://<your-domain>/alexa`, certificate:
   "My development endpoint has a certificate from a trusted certificate authority".
4. Put the skill id (`amzn1.ask.skill....`) into `.env` as `SKILL_JARVIS` or `SKILL_CLAUDE`,
   restart the container.
5. *Test* -> enable "Development". The skill then works on all Echos of the account,
   no certification needed.

## Test

```bash
python test_app.py
```
