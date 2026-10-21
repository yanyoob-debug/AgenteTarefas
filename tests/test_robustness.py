"""Entradas adversas, concorrência, limites e falhas de serviços (sem contas reais)."""

import asyncio
import os
import random
import unittest
from copy import deepcopy
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
import requests
from fastapi.testclient import TestClient
from google.adk.tools import FunctionTool

import webapp
from task_agent import agent, tools


class RobustnessTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"TRELLO_API_KEY": "", "TRELLO_TOKEN": "", "GOOGLE_API_KEY": "", "APP_TIMEZONE": "America/Sao_Paulo"})
        env.start()
        self.addCleanup(env.stop)
        saved = deepcopy(webapp.demo_tasks)
        self.addCleanup(lambda: webapp.demo_tasks.__setitem__(slice(None), saved))
        self.client = TestClient(webapp.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_fuzz_wrong_types_and_missing_fields(self):
        bad = [None, [], {}, 42, True]
        for field in ("nome", "descricao", "data"):
            for value in bad:
                body = {"nome": "Teste", "descricao": "Detalhes", "data": "2026-10-20", field: value}
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.client.post("/api/tasks?mode=demo", json=body).status_code, 422)
        for body in ({}, {"nome": "Teste"}, {"data": "2026-10-20"}):
            self.assertEqual(self.client.post("/api/tasks?mode=demo", json=body).status_code, 422)

    def test_boundary_text_lengths_and_unicode(self):
        for length, expected in ((0, 422), (250, 201), (251, 422)):
            self.assertEqual(self.client.post("/api/tasks?mode=demo", json={"nome": "a" * length, "data": "2026-10-20"}).status_code, expected)
        for length, expected in ((5000, 201), (5001, 422)):
            self.assertEqual(self.client.post("/api/tasks?mode=demo", json={"nome": "Teste 📚 漢字", "descricao": "x" * length, "data": "2026-10-20"}).status_code, expected)
        self.assertEqual(self.client.post("/api/tasks?mode=demo", json={"nome": " \n\t ", "data": "2026-10-20"}).status_code, 400)
        for length, expected in ((4000, 200), (4001, 422)):
            self.assertEqual(self.client.post("/api/chat?mode=demo", json={"message": "x" * length}).status_code, expected)

    def test_malformed_json(self):
        for body in ('{', '{"nome":', 'null', '[]'):
            self.assertEqual(self.client.post("/api/tasks?mode=demo", content=body, headers={"Content-Type": "application/json"}).status_code, 422)

    def test_unknown_card_cannot_modify_others(self):
        before = deepcopy(webapp.demo_tasks)
        self.assertEqual(self.client.patch("/api/tasks/not-found/status?mode=demo", json={"status": "Concluído"}).status_code, 404)
        self.assertEqual(before, webapp.demo_tasks)

    def test_demo_does_not_write_to_live_services(self):
        with patch.object(webapp.tools, "criar_tarefa") as create, patch.object(webapp.tools, "mover_tarefa") as move:
            result = self.client.post("/api/tasks?mode=demo", json={"nome": "Exemplo", "data": "2026-10-20"})
            self.client.patch(f"/api/tasks/{result.json()['tarefa']['id']}/status?mode=demo", json={"status": "Em andamento"})
            create.assert_not_called()
            move.assert_not_called()

    def test_http_methods_host_and_origin(self):
        self.assertEqual(self.client.delete("/api/tasks/demo-1").status_code, 404)
        self.assertEqual(self.client.delete("/api/tasks/demo-1/status").status_code, 405)
        self.assertEqual(self.client.get("/api/config", headers={"Host": "evil.example"}).status_code, 400)
        self.assertEqual(self.client.post("/api/chat?mode=demo", json={"message": "Oi"}, headers={"Origin": "null"}).status_code, 403)
        self.assertEqual(self.client.post("/api/chat?mode=demo", json={"message": "Oi"}, headers={"Origin": "http://testserver"}).status_code, 200)

    def test_security_headers_and_encoded_traversal(self):
        for path in ("/", "/static/app.js", "/api/config", "/not-found"):
            response = self.client.get(path)
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        for path in ("/static/%2e%2e/.env", "/static/%2e%2e%2f.env", "/static/%2e%2e/webapp.py", "/.git/config"):
            self.assertIn(self.client.get(path).status_code, (400, 404))

    def test_session_id_validation(self):
        for session_id in ("../.env", "a" * 33, "A" * 32, "", 123):
            self.assertEqual(self.client.post("/api/chat?mode=demo", json={"message": "Oi", "session_id": session_id}).status_code, 422)

    def test_session_storage_failure_has_safe_error(self):
        env = {"TRELLO_API_KEY": "fake", "TRELLO_TOKEN": "fake", "GOOGLE_API_KEY": "fake"}
        with patch.dict(os.environ, env), patch.object(webapp.sessions, "get_session", side_effect=RuntimeError("token=SEGREDO")):
            result = self.client.post("/api/chat?mode=live", json={"message": "Oi"})
        self.assertEqual(result.status_code, 502)
        self.assertNotIn("SEGREDO", result.text)

    def test_invalid_timezone_is_reported_in_configuration(self):
        with patch.dict(os.environ, {"APP_TIMEZONE": "inexistente"}):
            response = self.client.get("/api/config")
            self.assertEqual(response.status_code, 503)
            self.assertIn("APP_TIMEZONE", response.json()["detail"])

    def test_seed_tolerates_invalid_timezone_so_ui_can_show_error(self):
        with patch.dict(os.environ, {"APP_TIMEZONE": "inexistente"}):
            self.assertEqual(webapp._seed(), [])

    def test_random_date_roundtrips_in_five_timezones(self):
        randomizer = random.Random(42)
        for zone in ("America/Sao_Paulo", "America/Los_Angeles", "Pacific/Kiritimati", "Asia/Kolkata", "Europe/Lisbon"):
            with patch.dict(os.environ, {"APP_TIMEZONE": zone}):
                for _ in range(50):
                    day = date(2024, 1, 1) + timedelta(days=randomizer.randrange(1461))
                    local = datetime.fromisoformat(tools._due_utc(day.isoformat()).replace("Z", "+00:00")).astimezone(ZoneInfo(zone))
                    self.assertEqual(local.date(), day)
                    self.assertEqual(local.hour, 18)

    def test_trello_transport_uses_timeout_and_does_not_retry_writes(self):
        env = {"TRELLO_API_KEY": "fake", "TRELLO_TOKEN": "fake", "TRELLO_BOARD_ID": "", "TRELLO_BOARD_NAME": "DIO"}
        with patch.dict(os.environ, env), patch.object(requests.sessions.Session, "request") as request:
            request.return_value = SimpleNamespace(status_code=200, json=lambda: [])
            tools.listar_tarefas()
            self.assertEqual(request.call_args.kwargs.get("timeout"), (5, 20))
        board = MagicMock()
        todo, doing, done = MagicMock(), MagicMock(), MagicMock()
        todo.name, doing.name, done.name = "A fazer", "Em andamento", "Concluído"
        board.open_lists.return_value = [todo, doing, done]
        todo.add_card.side_effect = requests.Timeout("token=SEGREDO")
        with patch.object(tools, "_board", return_value=board):
            result = tools.criar_tarefa("Teste", "", "2026-10-20")
        self.assertFalse(result["ok"])
        self.assertNotIn("SEGREDO", str(result))
        todo.add_card.assert_called_once()


class ConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original = deepcopy(webapp.demo_tasks)

    async def asyncTearDown(self):
        webapp.demo_tasks[:] = self.original

    async def test_40_parallel_creations_do_not_lose_tasks_or_repeat_ids(self):
        transport = httpx.ASGITransport(app=webapp.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            responses = await asyncio.gather(*[client.post("/api/tasks?mode=demo", json={"nome": f"Carga {i}", "data": "2026-10-20"}) for i in range(40)])
            self.assertTrue(all(r.status_code == 201 for r in responses))
            ids = [r.json()["tarefa"]["id"] for r in responses]
            self.assertEqual(len(set(ids)), 40)
            self.assertEqual(len(webapp.demo_tasks), len(self.original) + 40)

    async def test_adk_sync_tool_does_not_block_event_loop(self):
        import time
        from threading import Event
        heartbeat = Event()

        def slow_board():
            time.sleep(0.08)
            raise tools.TaskError("Falha simulada no teste")

        async def pulse():
            await asyncio.sleep(0.01)
            heartbeat.set()

        registered = next(function for function in agent.root_agent.tools if function.__name__ == "listar_tarefas")
        with patch.object(tools, "_board", side_effect=slow_board):
            tool_task = asyncio.create_task(FunctionTool(func=registered).run_async(args={}, tool_context=None))
            pulse_task = asyncio.create_task(pulse())
            try:
                await tool_task
                self.assertTrue(heartbeat.is_set(), "A ferramenta síncrona bloqueou o servidor durante a chamada ao Trello.")
            finally:
                await pulse_task


if __name__ == "__main__":
    unittest.main()
