from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import Agent
from .client import OpenRouterClient
from .config import Config, ConfigError, load_config
from .tools import build_default_registry

WEB_DIR = Path(__file__).resolve().parent / "web"


class SessionCreate(BaseModel):
    project_dir: str = "."
    model: str | None = None
    api_key: str | None = None
    max_turns: int = 25
    stream: bool = True


class ChatRequest(BaseModel):
    message: str


def default_agent_factory(config: Config) -> Agent:
    client = OpenRouterClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        timeout=config.timeout,
    )
    registry = build_default_registry(
        config.project_dir,
        max_tool_output=config.max_tool_output,
        secret=config.api_key,
    )
    return Agent(client, registry, config)


def create_app(agent_factory: Callable[[Config], Agent] | None = None) -> FastAPI:
    app = FastAPI(title="Gem Agent")
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    factory = agent_factory or default_agent_factory
    sessions: dict[str, Agent] = {}
    locks: dict[str, threading.Lock] = {}
    meta_lock = threading.Lock()

    def get_agent(session_id: str) -> Agent:
        with meta_lock:
            agent = sessions.get(session_id)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"unknown session '{session_id}'")
        return agent

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/sessions", status_code=201)
    def create_session(body: SessionCreate) -> JSONResponse:
        try:
            config = load_config(
                project_dir=body.project_dir,
                model=body.model,
                api_key=body.api_key,
                max_turns=body.max_turns,
                stream=body.stream,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        agent = factory(config)
        session_id = uuid.uuid4().hex[:12]
        with meta_lock:
            sessions[session_id] = agent
            locks[session_id] = threading.Lock()
        return JSONResponse(
            {
                "session_id": session_id,
                "project_dir": str(config.project_dir),
                "model": config.model,
                "max_turns": config.max_turns,
                "stream": config.stream,
            },
            status_code=201,
        )

    @app.get("/api/sessions/{session_id}")
    def session_info(session_id: str) -> dict[str, Any]:
        agent = get_agent(session_id)
        return {
            "session_id": session_id,
            "project_dir": str(agent.config.project_dir),
            "model": agent.config.model,
            "max_turns": agent.config.max_turns,
            "stream": agent.config.stream,
            "message_count": len(agent.messages),
        }

    @app.get("/api/sessions/{session_id}/history")
    def session_history(session_id: str) -> dict[str, Any]:
        agent = get_agent(session_id)
        return {"messages": agent.messages}

    @app.post("/api/sessions/{session_id}/reset")
    def reset_session(session_id: str) -> dict[str, bool]:
        get_agent(session_id).reset()
        return {"ok": True}

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, bool]:
        with meta_lock:
            sessions.pop(session_id, None)
            locks.pop(session_id, None)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/chat")
    def chat(session_id: str, body: ChatRequest) -> StreamingResponse:
        agent = get_agent(session_id)
        lock = locks[session_id]

        def event_stream():
            with lock:
                try:
                    for event in agent.run_events(body.message):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except Exception as exc:
                    yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
