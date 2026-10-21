"""Ferramentas do agente: regras em Python, persistência no Trello."""

import os
import re
import unicodedata
from datetime import date, datetime, time, timezone
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from trello import TrelloClient


class TaskError(Exception):
    """Erro que pode ser mostrado na conversa sem expor credenciais."""


class _TrelloHttp:
    """Transporte de fetch_json com limites de conexão e leitura, sem retentativas."""

    @staticmethod
    def request(method, url, **kwargs):
        kwargs["timeout"] = (5, 20)
        return requests.request(method, url, **kwargs)


def _safe_tool(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except TaskError as exc:
            return {"ok": False, "erro": str(exc)}
        except Exception:
            # Exceções HTTP podem conter a URL com chave e token.
            return {
                "ok": False,
                "erro": "Falha ao acessar o Trello. Confira credenciais, permissões e conexão. "
                "Se a operação alterava um card, liste as tarefas antes de tentar novamente.",
            }
    return wrapped


def _zone():
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE", "America/Sao_Paulo"))
    except (ZoneInfoNotFoundError, ValueError):
        raise TaskError("APP_TIMEZONE inválido. Use um fuso como America/Sao_Paulo.") from None


def _normalize(text):
    text = unicodedata.normalize("NFKD", text.strip().casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def _status(value):
    aliases = {
        "a fazer": "A fazer", "pendente": "A fazer", "pendentes": "A fazer",
        "em andamento": "Em andamento", "andamento": "Em andamento",
        "concluido": "Concluído", "concluida": "Concluído",
        "concluidos": "Concluído", "concluidas": "Concluído",
        "feito": "Concluído", "feita": "Concluído", "finalizado": "Concluído",
    }
    result = aliases.get(_normalize(value))
    if not result:
        raise TaskError("Status inválido. Use A fazer, Em andamento ou Concluído.")
    return result


def _due_utc(value):
    """Interpreta uma data local às 18h e a converte em um instante UTC."""
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        day = date.fromisoformat(value)
    except ValueError:
        raise TaskError("Data inválida. Use uma data existente no formato AAAA-MM-DD.") from None
    local = datetime.combine(day, time(18), tzinfo=_zone())
    return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _board():
    key = os.getenv("TRELLO_API_KEY", "").strip()
    token = os.getenv("TRELLO_TOKEN", "").strip()
    if not key or not token:
        raise TaskError("Preencha TRELLO_API_KEY e TRELLO_TOKEN no arquivo .env.")
    client = TrelloClient(
        api_key=key,
        api_secret=os.getenv("TRELLO_API_SECRET") or None,
        token=token,
        http_service=_TrelloHttp,
    )
    board_id = os.getenv("TRELLO_BOARD_ID", "").strip()
    if board_id:
        board = client.get_board(board_id)
        if board.closed:
            raise TaskError("O quadro configurado está arquivado.")
        return board
    name = os.getenv("TRELLO_BOARD_NAME", "DIO").strip()
    boards = [b for b in client.list_boards(board_filter="open") if b.name == name]
    if not boards:
        raise TaskError("Quadro não encontrado. Confira TRELLO_BOARD_NAME ou TRELLO_BOARD_ID.")
    if len(boards) > 1:
        raise TaskError("Há quadros com o mesmo nome. Configure TRELLO_BOARD_ID.")
    return boards[0]


def _lists(board):
    result = {}
    for item in board.open_lists():
        try:
            name = _status(item.name)
        except TaskError:
            continue
        if name in result:
            raise TaskError(f"Há mais de uma lista para {name}. Ajuste os nomes no quadro.")
        result[name] = item
    missing = set(("A fazer", "Em andamento", "Concluído")) - result.keys()
    if missing:
        raise TaskError("Crie as listas que faltam no quadro: " + ", ".join(sorted(missing)))
    return result


def _card_data(card, status):
    due = card.due
    if due:
        if isinstance(due, str):
            due = datetime.fromisoformat(due.replace("Z", "+00:00"))
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        due = due.astimezone(_zone()).isoformat()
    return {
        "id": card.id, "nome": card.name, "descricao": card.description,
        "status": status, "vencimento": due or None, "url": card.url,
    }


@_safe_tool
def obter_data_atual() -> dict:
    """Obtém a data e o fuso locais para interpretar hoje e amanhã."""
    now = datetime.now(_zone())
    return {"ok": True, "data": now.date().isoformat(), "data_br": now.strftime("%d/%m/%Y"),
            "fuso": str(_zone())}


@_safe_tool
def criar_tarefa(nome: str, descricao: str, data: str) -> dict:
    """Cria um card na lista A fazer do quadro configurado.

    Args:
        nome: Título não vazio da tarefa.
        descricao: Detalhes fornecidos pelo usuário; use vazio se não houver.
        data: Vencimento local no formato AAAA-MM-DD, às 18h.
    """
    if not nome.strip():
        raise TaskError("O nome da tarefa não pode ficar vazio.")
    due = _due_utc(data)
    lists = _lists(_board())
    card = lists["A fazer"].add_card(nome.strip(), desc=descricao.strip(), due=due)
    return {"ok": True, "tarefa": _card_data(card, "A fazer")}


@_safe_tool
def listar_tarefas(status: str = "todas") -> dict:
    """Lista cards ativos, de todos os status ou de uma lista específica.

    Args:
        status: todas, A fazer, Em andamento ou Concluído; aceita acentos e variações.
    """
    selected = None if _normalize(status) in ("", "todas", "todos") else _status(status)
    lists = _lists(_board())
    tasks = []
    for name, trello_list in lists.items():
        if selected is None or selected == name:
            tasks.extend(_card_data(c, name) for c in trello_list.list_cards())
    return {"ok": True, "tarefas": tasks, "total": len(tasks)}


@_safe_tool
def mover_tarefa(tarefa_id: str, status: str) -> dict:
    """Move um card ativo do quadro configurado para outro status.

    Args:
        tarefa_id: ID exato retornado por listar_tarefas ou criar_tarefa.
        status: A fazer, Em andamento ou Concluído; aceita concluido e concluída.
    """
    target = _status(status)
    lists = _lists(_board())
    for trello_list in lists.values():
        for card in trello_list.list_cards():
            if card.id == tarefa_id:
                card.change_list(lists[target].id)
                return {"ok": True, "tarefa": _card_data(card, target)}
    raise TaskError("Tarefa ativa não encontrada nas listas deste quadro. Liste as tarefas novamente.")
