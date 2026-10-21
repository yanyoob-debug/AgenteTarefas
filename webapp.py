"""Interface local: execute python -m uvicorn webapp:app --host 127.0.0.1."""

import asyncio
import os
from copy import deepcopy
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from task_agent.agent import root_agent
from task_agent import tools

ROOT = Path(__file__).resolve().parent
Mode = Literal["demo", "live"]
STATUSES = ("A fazer", "Em andamento", "Concluído")
app = FastAPI(title="Dia — seu assistente de tarefas", docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")
sessions = InMemorySessionService()
runner = Runner(agent=root_agent, app_name="task_agent", session_service=sessions)
chat_lock = asyncio.Lock()


def _today():
    result = tools.obter_data_atual()
    if not result["ok"]:
        raise HTTPException(503, result["erro"])
    return result


def _configured():
    return all(os.getenv(key, "").strip() for key in ("TRELLO_API_KEY", "TRELLO_TOKEN"))


def _require_live():
    if not _configured():
        raise HTTPException(503, "Configure as credenciais do Trello no .env e reinicie o servidor.")


def _result(result):
    if not result["ok"]:
        raise HTTPException(400, result["erro"])
    return result


def _seed():
    try:
        due = tools._due_utc(_today()["data"])
    except (HTTPException, tools.TaskError):
        # Deixe a página abrir e /api/config explicar a configuração incorreta.
        return []
    return [
        {"id": "demo-1", "nome": "Estudar funções em Python", "descricao": "Praticar os exercícios e anotar o que aprendi.", "status": "A fazer", "vencimento": due, "url": None},
        {"id": "demo-2", "nome": "Organizar as ideias do projeto", "descricao": "Separar as próximas etapas do agente de tarefas.", "status": "A fazer", "vencimento": due, "url": None},
        {"id": "demo-3", "nome": "Construir meu agente de IA", "descricao": "Conectar a conversa às ferramentas do Trello.", "status": "Em andamento", "vencimento": due, "url": None},
        {"id": "demo-4", "nome": "Preparar o ambiente", "descricao": "Ambiente virtual e dependências instalados.", "status": "Concluído", "vencimento": due, "url": None},
    ]


demo_tasks = _seed()


@app.middleware("http")
async def same_origin(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host')}"
        if origin and origin != expected:
            return JSONResponse({"detail": "Origem não permitida."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response


@app.get("/")
def index():
    return FileResponse(ROOT / "frontend" / "index.html")


@app.get("/api/config")
def config():
    return {"today": _today(), "trello_ready": _configured(),
            "gemini_ready": bool(os.getenv("GOOGLE_API_KEY", "").strip()),
            "board_name": os.getenv("TRELLO_BOARD_NAME", "DIO"),
            "default_mode": "live" if _configured() else "demo"}


@app.get("/api/tasks")
async def list_tasks(mode: Mode = "live"):
    if mode == "demo":
        return {"ok": True, "tarefas": deepcopy(demo_tasks), "total": len(demo_tasks)}
    _require_live()
    return _result(await run_in_threadpool(tools.listar_tarefas))


class NewTask(BaseModel):
    nome: str = Field(min_length=1, max_length=250)
    descricao: str = Field(default="", max_length=5000)
    data: str = Field(min_length=10, max_length=10)


@app.post("/api/tasks", status_code=201)
async def create_task(body: NewTask, mode: Mode = "live"):
    if mode == "demo":
        try:
            due = tools._due_utc(body.data)
            if not body.nome.strip():
                raise tools.TaskError("Informe o nome da tarefa.")
        except tools.TaskError as exc:
            raise HTTPException(400, str(exc)) from None
        task = {"id": "demo-" + uuid4().hex, "nome": body.nome.strip(),
                "descricao": body.descricao.strip(), "status": "A fazer", "vencimento": due, "url": None}
        demo_tasks.append(task)
        return {"ok": True, "tarefa": deepcopy(task)}
    _require_live()
    return _result(await run_in_threadpool(tools.criar_tarefa, body.nome, body.descricao, body.data))


class MoveTask(BaseModel):
    status: Literal["A fazer", "Em andamento", "Concluído"]


@app.patch("/api/tasks/{task_id}/status")
async def move_task(task_id: str, body: MoveTask, mode: Mode = "live"):
    if mode == "demo":
        for task in demo_tasks:
            if task["id"] == task_id:
                task["status"] = body.status
                return {"ok": True, "tarefa": deepcopy(task)}
        raise HTTPException(404, "Tarefa de demonstração não encontrada.")
    _require_live()
    return _result(await run_in_threadpool(tools.mover_tarefa, task_id, body.status))


class ChatMessage(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


async def _agent_reply(message, session_id):
    reply = []
    async for event in runner.run_async(
        user_id="local", session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
        if event.is_final_response() and event.content:
            reply.extend(part.text for part in event.content.parts or [] if part.text)
    return reply


@app.post("/api/chat")
async def chat(body: ChatMessage, mode: Mode = "live"):
    if not body.message.strip():
        raise HTTPException(400, "Escreva uma mensagem.")
    if mode == "demo":
        return {"ok": True, "reply": "Você está na demonstração, sem chamadas à IA. "
                "Experimente criar uma tarefa no botão Nova tarefa e mudar seu status no card. "
                "Para conversar com o Gemini, configure o .env e selecione Trello conectado.", "session_id": None}
    _require_live()
    if not os.getenv("GOOGLE_API_KEY", "").strip():
        raise HTTPException(503, "Configure GOOGLE_API_KEY no .env e reinicie para usar o chat.")
    async with chat_lock:
        session_id = body.session_id or uuid4().hex
        try:
            session = await sessions.get_session(app_name="task_agent", user_id="local", session_id=session_id)
            if session is None:
                await sessions.create_session(app_name="task_agent", user_id="local", session_id=session_id)
            reply = await asyncio.wait_for(_agent_reply(body.message, session_id), timeout=90)
        except Exception:
            # Não propagar URLs, tokens ou detalhes da API ao navegador.
            raise HTTPException(502, "Não consegui concluir a conversa. Confira a chave e a cota do Gemini. "
                                "Atualize o quadro antes de repetir: alguma ação pode ter sido realizada.") from None
        return {"ok": True, "reply": "\n".join(reply) or "Não recebi uma resposta. Confira o quadro antes de tentar novamente.",
                "session_id": session_id}
