"""Verifica os contratos HTTP e a separação entre demonstração e Trello."""

import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.genai import types

import webapp


class WebAppTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"TRELLO_API_KEY": "", "TRELLO_TOKEN": "", "GOOGLE_API_KEY": ""})
        env.start()
        self.addCleanup(env.stop)
        original = deepcopy(webapp.demo_tasks)
        self.addCleanup(lambda: webapp.demo_tasks.__setitem__(slice(None), original))
        self.client = TestClient(webapp.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_home_and_static_assets(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('lang="pt-BR"', response.text)
        for path in ("/static/app.js", "/static/style.css", "/static/favicon.svg"):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get("/.env").status_code, 404)
        self.assertEqual(self.client.get("/static/../.env").status_code, 404)

    def test_default_demo_does_not_expose_secrets(self):
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "GEMINI_SEGREDO"}):
            response = self.client.get("/api/config")
        self.assertEqual(response.json()["default_mode"], "demo")
        self.assertNotIn("GEMINI_SEGREDO", response.text)

    def test_demo_create_list_move_without_trello(self):
        with patch.object(webapp.tools, "_board") as board:
            result = self.client.post("/api/tasks?mode=demo", json={
                "nome": "Teste do front", "descricao": "Descrição", "data": "2026-10-20",
            })
            self.assertEqual(result.status_code, 201)
            task = result.json()["tarefa"]
            self.assertEqual(task["status"], "A fazer")
            result = self.client.patch(f"/api/tasks/{task['id']}/status?mode=demo", json={"status": "Concluído"})
            self.assertEqual(result.json()["tarefa"]["status"], "Concluído")
            tasks = self.client.get("/api/tasks?mode=demo").json()["tarefas"]
            self.assertTrue(any(t["id"] == task["id"] and t["status"] == "Concluído" for t in tasks))
            board.assert_not_called()

    def test_live_mode_requires_credentials(self):
        self.assertEqual(self.client.get("/api/tasks?mode=live").status_code, 503)
        self.assertEqual(self.client.post("/api/chat?mode=live", json={"message": "Oi"}).status_code, 503)

    def test_live_tasks_use_existing_tools(self):
        with patch.dict(os.environ, {"TRELLO_API_KEY": "fake", "TRELLO_TOKEN": "fake"}), \
             patch.object(webapp.tools, "listar_tarefas", return_value={"ok": True, "tarefas": [], "total": 0}) as listing:
            self.assertEqual(self.client.get("/api/tasks?mode=live").json()["total"], 0)
            listing.assert_called_once()

    def test_live_chat_keeps_session_and_uses_runner(self):
        calls = []

        async def fake_run(**kwargs):
            calls.append(kwargs)
            yield SimpleNamespace(is_final_response=lambda: True,
                                  content=types.Content(parts=[types.Part(text="Olá! Quais são suas tarefas?")]))

        env = {"TRELLO_API_KEY": "fake", "TRELLO_TOKEN": "fake", "GOOGLE_API_KEY": "fake"}
        with patch.dict(os.environ, env), patch.object(webapp.runner, "run_async", side_effect=fake_run):
            first = self.client.post("/api/chat?mode=live", json={"message": "Oi"})
            self.assertEqual(first.status_code, 200)
            session = first.json()["session_id"]
            self.assertIn("Quais são", first.json()["reply"])
            second = self.client.post("/api/chat?mode=live", json={"message": "Estudar", "session_id": session})
            self.assertEqual(second.json()["session_id"], session)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1]["new_message"].parts[0].text, "Estudar")

    def test_demo_chat_is_explicit_and_never_calls_gemini(self):
        with patch.object(webapp.runner, "run_async") as run:
            result = self.client.post("/api/chat?mode=demo", json={"message": "Crie uma tarefa"})
            self.assertIn("sem chamadas à IA", result.json()["reply"])
            run.assert_not_called()

    def test_api_rejects_invalid_dates_status_and_empty_message(self):
        response = self.client.post("/api/tasks?mode=demo", json={"nome": "Teste", "data": "2026-02-30"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.patch("/api/tasks/demo-1/status?mode=demo", json={"status": "Apagado"}).status_code, 422)
        self.assertEqual(self.client.post("/api/chat?mode=demo", json={"message": "  "}).status_code, 400)
        self.assertEqual(self.client.get("/api/tasks?mode=other").status_code, 422)

    def test_cross_origin_mutation_is_rejected(self):
        result = self.client.post("/api/tasks?mode=demo", headers={"Origin": "https://outro-site.example"}, json={"nome": "Teste", "data": "2026-10-20"})
        self.assertEqual(result.status_code, 403)

    def test_chat_failure_does_not_expose_api_secrets(self):
        async def fail(**kwargs):
            raise RuntimeError("URL?token=SEGREDO")
            yield

        env = {"TRELLO_API_KEY": "fake", "TRELLO_TOKEN": "fake", "GOOGLE_API_KEY": "fake"}
        with patch.dict(os.environ, env), patch.object(webapp.runner, "run_async", side_effect=fail):
            result = self.client.post("/api/chat?mode=live", json={"message": "Oi"})
        self.assertEqual(result.status_code, 502)
        self.assertNotIn("SEGREDO", result.text)


if __name__ == "__main__":
    unittest.main()
