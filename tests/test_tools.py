"""Testes sem rede: simulam apenas a fronteira com o Trello."""

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from google.adk.tools import FunctionTool
from trello.card import Card

from task_agent import agent, tools


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"APP_TIMEZONE": "America/Sao_Paulo"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.card = MagicMock(spec=Card,
            id="card-1", name="Estudar Python", description="Ler funções",
            due="2026-10-20T21:00:00Z", url="https://trello.com/c/exemplo",
        )
        self.card.name = "Estudar Python"
        self.todo = MagicMock(id="list-1", name="A fazer")
        self.doing = MagicMock(id="list-2", name="Em andamento")
        self.done = MagicMock(id="list-3", name="Concluído")
        # MagicMock reserva name para sua própria representação.
        self.todo.name, self.doing.name, self.done.name = "A fazer", "Em andamento", "Concluído"
        self.todo.list_cards.return_value = [self.card]
        self.doing.list_cards.return_value = []
        self.done.list_cards.return_value = []
        self.todo.add_card.return_value = self.card
        self.board = MagicMock()
        self.board.open_lists.return_value = [self.todo, self.doing, self.done]

    def test_due_keeps_local_day_instead_of_previous_day(self):
        due = tools._due_utc("2026-10-20")
        self.assertEqual(due, "2026-10-20T21:00:00Z")
        local = datetime.fromisoformat(due.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Sao_Paulo"))
        self.assertEqual(local.strftime("%Y-%m-%d %H:%M"), "2026-10-20 18:00")

    def test_timezone_with_next_day_utc(self):
        with patch.dict(os.environ, {"APP_TIMEZONE": "America/Los_Angeles"}):
            self.assertEqual(tools._due_utc("2026-10-20"), "2026-10-21T01:00:00Z")

    def test_invalid_dates_do_not_contact_trello(self):
        with patch.object(tools, "_board") as board:
            for value in ("2026-02-30", "20/10/2026", "2026-1-1", "amanhã"):
                with self.subTest(value=value):
                    self.assertFalse(tools.criar_tarefa("Estudar", "", value)["ok"])
            board.assert_not_called()

    def test_accented_and_unaccented_status(self):
        for value in ("concluído", "CONCLUIDO", " concluída ", "feito", "concluídas"):
            self.assertEqual(tools._status(value), "Concluído")

    def test_create_sends_description_and_timezone_to_trello(self):
        with patch.object(tools, "_board", return_value=self.board):
            result = tools.criar_tarefa(" Estudar Python ", " Ler funções ", "2026-10-20")
        self.assertTrue(result["ok"])
        self.todo.add_card.assert_called_once_with("Estudar Python", desc="Ler funções", due="2026-10-20T21:00:00Z")
        self.assertEqual(result["tarefa"]["vencimento"], "2026-10-20T18:00:00-03:00")

    def test_blank_name(self):
        with patch.object(tools, "_board") as board:
            self.assertFalse(tools.criar_tarefa("  ", "", "2026-10-20")["ok"])
            board.assert_not_called()

    def test_list_all(self):
        with patch.object(tools, "_board", return_value=self.board):
            result = tools.listar_tarefas()
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["tarefas"][0]["id"], "card-1")
        self.done.list_cards.assert_called_once()

    def test_list_filters_status(self):
        with patch.object(tools, "_board", return_value=self.board):
            result = tools.listar_tarefas("concluido")
        self.assertEqual(result, {"ok": True, "tarefas": [], "total": 0})
        self.todo.list_cards.assert_not_called()

    def test_move_by_id(self):
        with patch.object(tools, "_board", return_value=self.board):
            result = tools.mover_tarefa("card-1", "concluída")
        self.assertTrue(result["ok"])
        self.card.change_list.assert_called_once_with("list-3")

    def test_move_rejects_card_outside_board(self):
        with patch.object(tools, "_board", return_value=self.board):
            self.assertFalse(tools.mover_tarefa("outro-card", "concluído")["ok"])
        self.card.change_list.assert_not_called()

    def test_invalid_status_does_not_contact_trello(self):
        with patch.object(tools, "_board") as board:
            self.assertFalse(tools.mover_tarefa("card-1", "apagado")["ok"])
            self.assertFalse(tools.listar_tarefas("apagado")["ok"])
            board.assert_not_called()

    def test_missing_and_duplicate_lists(self):
        for lists in ([self.todo], [self.todo, self.todo, self.doing, self.done]):
            self.board.open_lists.return_value = lists
            with patch.object(tools, "_board", return_value=self.board):
                self.assertFalse(tools.listar_tarefas()["ok"])

    def test_api_errors_never_expose_credentials(self):
        with patch.object(tools, "_board", side_effect=RuntimeError("URL?token=SEGREDO")):
            for result in (tools.listar_tarefas(), tools.criar_tarefa("Estudar", "", "2026-10-20"),
                           tools.mover_tarefa("card-1", "feito")):
                self.assertFalse(result["ok"])
                self.assertNotIn("SEGREDO", str(result))

    def test_missing_credentials(self):
        with patch.dict(os.environ, {"TRELLO_API_KEY": "", "TRELLO_TOKEN": ""}):
            self.assertIn("Preencha", tools.listar_tarefas()["erro"])

    def test_duplicate_boards_require_id(self):
        env = {"TRELLO_API_KEY": "fake", "TRELLO_TOKEN": "fake", "TRELLO_BOARD_ID": "", "TRELLO_BOARD_NAME": "DIO"}
        with patch.dict(os.environ, env), patch.object(tools, "TrelloClient", autospec=True) as client:
            client.return_value.list_boards.return_value = [SimpleNamespace(name="DIO"), SimpleNamespace(name="DIO")]
            self.assertIn("TRELLO_BOARD_ID", tools.listar_tarefas()["erro"])

    def test_board_id_takes_precedence(self):
        env = {"TRELLO_API_KEY": "fake", "TRELLO_TOKEN": "fake", "TRELLO_BOARD_ID": "board-1"}
        with patch.dict(os.environ, env), patch.object(tools, "TrelloClient", autospec=True) as client:
            client.return_value.get_board.return_value = SimpleNamespace(closed=False)
            tools._board()
            client.return_value.get_board.assert_called_once_with("board-1")
            client.return_value.list_boards.assert_not_called()

    def test_invalid_timezone_returns_actionable_error(self):
        with patch.dict(os.environ, {"APP_TIMEZONE": "fuso-inexistente"}):
            self.assertIn("APP_TIMEZONE", tools.obter_data_atual()["erro"])

    def test_instruction_recomputes_today(self):
        for iso, br in (("2026-10-20", "20/10/2026"), ("2026-10-21", "21/10/2026")):
            with patch.object(agent, "obter_data_atual", return_value={
                "ok": True, "data": iso, "data_br": br, "fuso": "America/Sao_Paulo",
            }):
                self.assertIn(br, agent.instruction(None))

    def test_adk_builds_declarations_for_all_tools(self):
        for function in agent.root_agent.tools:
            with self.subTest(tool=function.__name__):
                declaration = FunctionTool(func=function)._get_declaration()
                self.assertEqual(declaration.name, function.__name__)
                self.assertTrue(declaration.description)
        create = FunctionTool(func=tools.criar_tarefa)._get_declaration()
        self.assertEqual(set(create.parameters.properties), {"nome", "descricao", "data"})


if __name__ == "__main__":
    unittest.main()
