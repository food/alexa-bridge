"""HTTP entry point: the skill endpoint and a health check."""
from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool

from . import config, skill

app = FastAPI()


@app.post("/alexa")
async def alexa(request: Request):
    """Skill endpoint as entered in the Amazon developer console."""
    return await run_in_threadpool(skill.handle, dict(request.headers), await request.body())


@app.get("/health")
def health():
    """Liveness check for Traefik and Docker."""
    return {"ok": True, "skills": sorted(config.SKILLS.values())}
