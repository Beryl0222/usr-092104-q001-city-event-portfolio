"""HTTP API 冒烟测试：受理、申请方视图、决定约束与审计回放。"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from src.api import make_server
from tests.helpers import make_service


def _load_sample() -> dict:
    path = Path(__file__).parents[1] / "data" / "sample_application.json"
    return json.loads(path.read_text(encoding="utf-8"))


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = make_server(make_service(), "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_file_and_applicant_view(self) -> None:
        status, created = self._call("POST", "/applications", _load_sample())
        self.assertEqual(status, 201)
        app_id = created["application_id"]
        self.assertEqual(created["status"], "in_review")

        status, view = self._call("GET", f"/applications/{app_id}/applicant")
        self.assertEqual(status, 200)
        self.assertEqual(view["name"], "城市半程马拉松")
        self.assertEqual(view["decisions"], [])

        status, trail = self._call("GET", f"/audit/{app_id}")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(trail["event_count"], 1)

    def test_decision_without_authorized_department_rejected(self) -> None:
        _, created = self._call("POST", "/applications", _load_sample())
        app_id = created["application_id"]
        status, error = self._call(
            "POST",
            f"/applications/{app_id}/decision",
            {"outcome": "approved", "decided_by": "system", "rationale": "自动批准"},
        )
        self.assertEqual(status, 400)
        self.assertIn("有权部门", error["error"])

    def test_missing_evidence_visible_to_applicant(self) -> None:
        draft = _load_sample()
        draft["evidence"] = {"operator_license": "doc-1"}
        _, created = self._call("POST", "/applications", draft)
        app_id = created["application_id"]
        _, view = self._call("GET", f"/applications/{app_id}/applicant")
        self.assertEqual(view["status"], "supplement_required")
        self.assertIn("大型群众性活动安全许可", view["supplement_requests"][0]["missing"])

    def test_unknown_route_returns_404(self) -> None:
        status, _ = self._call("GET", "/no-such-path")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
