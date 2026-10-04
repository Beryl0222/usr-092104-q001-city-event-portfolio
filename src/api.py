"""承办审议后端的 HTTP 接口（仅标准库）。

运行：``python3 -m src.api [--host 127.0.0.1] [--port 8080] [--log events.jsonl]``

接口一览（请求与响应均为 JSON）：
- POST   /applications                              受理申办
- GET    /applications/{id}                         完整决策档案
- POST   /applications/{id}/versions                提交补件/新版本
- POST   /applications/{id}/withdraw                撤回申办
- GET    /applications/{id}/applicant               申请方视图（补正理由、决定版本）
- GET    /applications/{id}/conflicts               未协调冲突
- POST   /applications/{id}/opinions                会签
- POST   /applications/{id}/recusals                回避登记
- POST   /applications/{id}/decision                表决决定
- POST   /applications/{id}/conditions/{cid}/fulfill 条件达成登记
- POST   /applications/{id}/changes                 变更申报
- POST   /applications/{id}/changes/{cid}/close     变更审议收口
- POST   /conflicts/{id}/resolve                    冲突协调登记
- POST   /public-services/{id}/revoke               撤销公共服务承诺
- GET    /portfolio/{year}                          年度组合峰值压力
- GET    /audit/{id}                                审计回放
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from src.audit import build_audit_trail
from src.events import EventStore
from src.models import DomainError
from src.portfolio import build_portfolio_view
from src.service import ReviewService

Handler = Callable[[dict, dict], tuple[int, object]]


def _routes(service: ReviewService) -> list[tuple[str, re.Pattern, Handler]]:
    def route(method: str, pattern: str, handler: Handler):
        return method, re.compile(f"^{pattern}$"), handler

    return [
        route("POST", r"/applications", lambda body, m: (201, service.file_application(body))),
        route("GET", r"/applications/(?P<id>[^/]+)", lambda b, m: (200, service.decision_file(m["id"]))),
        route("POST", r"/applications/(?P<id>[^/]+)/versions", lambda b, m: (201, service.submit_version(m["id"], b))),
        route("POST", r"/applications/(?P<id>[^/]+)/withdraw", lambda b, m: (200, service.withdraw(m["id"], b.get("reason", "")))),
        route("GET", r"/applications/(?P<id>[^/]+)/applicant", lambda b, m: (200, service.applicant_view(m["id"]))),
        route("GET", r"/applications/(?P<id>[^/]+)/conflicts", lambda b, m: (200, {"conflicts": service.open_conflicts(m["id"])})),
        route("POST", r"/applications/(?P<id>[^/]+)/opinions", lambda b, m: (201, service.sign_opinion(m["id"], b["stage"], b["department"], b["signer"], b["position"], b.get("comment", "")))),
        route("POST", r"/applications/(?P<id>[^/]+)/recusals", lambda b, m: (201, service.declare_recusal(m["id"], b.get("person", ""), b.get("reason", ""), b.get("stage")))),
        route("POST", r"/applications/(?P<id>[^/]+)/decision", lambda b, m: (201, service.issue_decision(m["id"], b["outcome"], b.get("decided_by", ""), b.get("rationale", ""), b.get("conditions")))),
        route("POST", r"/applications/(?P<id>[^/]+)/conditions/(?P<cid>[^/]+)/fulfill", lambda b, m: (200, service.fulfill_condition(m["id"], m["cid"], b.get("evidence_note", "")))),
        route("POST", r"/applications/(?P<id>[^/]+)/changes", lambda b, m: (201, service.request_change(m["id"], b["kind"], b.get("description", ""), b["new_sections"], b.get("revocations")))),
        route("POST", r"/applications/(?P<id>[^/]+)/changes/(?P<cid>[^/]+)/close", lambda b, m: (200, service.close_change(m["id"], m["cid"], b.get("decided_by", ""), b.get("rationale", ""), b.get("outcome", "approved"), b.get("conditions")))),
        route("POST", r"/conflicts/(?P<id>[^/]+)/resolve", lambda b, m: (200, service.resolve_conflict(m["id"], b.get("resolution", ""), b.get("decided_by", "")))),
        route("POST", r"/public-services/(?P<id>[^/]+)/revoke", lambda b, m: (200, service.revoke_public_service(m["id"], b.get("reason", ""), b.get("authority", "")))),
        route("GET", r"/portfolio/(?P<year>\d{4})", lambda b, m: (200, build_portfolio_view(service, int(m["year"])))),
        route("GET", r"/audit/(?P<id>[^/]+)", lambda b, m: (200, build_audit_trail(service, m["id"]))),
    ]


def make_server(service: ReviewService, host: str, port: int) -> ThreadingHTTPServer:
    routes = _routes(service)

    class RequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _dispatch(self, method: str) -> None:
            try:
                body: dict = {}
                if method == "POST":
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b""
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                    if not isinstance(body, dict):
                        raise DomainError("请求体须为 JSON 对象")
                for route_method, pattern, handler in routes:
                    if route_method != method:
                        continue
                    match = pattern.match(self.path)
                    if match:
                        status, payload = handler(body, match.groupdict())
                        self._send(status, payload)
                        return
                self._send(404, {"error": f"路径不存在：{method} {self.path}"})
            except DomainError as exc:
                self._send(400, {"error": exc.message, "details": exc.details})
            except (KeyError, ValueError) as exc:
                self._send(400, {"error": f"请求参数不合法：{exc}"})
            except json.JSONDecodeError as exc:
                self._send(400, {"error": f"请求体不是合法 JSON：{exc}"})

        def _send(self, status: int, payload: object) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def log_message(self, fmt: str, *args: object) -> None:
            pass  # 静默访问日志，事件日志才是系统记录

    return ThreadingHTTPServer((host, port), RequestHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="城市赛事承办审议后端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--log", default=None, help="事件日志 JSONL 落盘路径")
    args = parser.parse_args()
    store = EventStore(path=args.log) if args.log else EventStore()
    service = ReviewService(store=store)
    server = make_server(service, args.host, args.port)
    print(f"承办审议后端已启动：http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
