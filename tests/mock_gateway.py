"""mock_gateway.py — a local OpenAI-format gateway for $0 end-to-end tests.

Run it, point a route at `openai/mock-spine` with OPENAI_API_BASE at this
server, and the ENTIRE stack — router → litellm → HTTP wire → tool-call
protocol → sanitizer → ledger settle → verification gate — executes for
real with no vendor, no key, no spend:

    python tests/mock_gateway.py 8089 &
    OPENAI_API_KEY=mock OPENAI_API_BASE=http://127.0.0.1:8089/v1 \
        python cli.py --config tests/mock.toml "write hello.txt"

It scripts a happy path: Amatya plans one subtask, Karta issues a
write_file tool call then reports, Amatya verifies. Extend the script
to rehearse failure ladders before they ever cost a token.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PLAN = {
    "plan": [
        {
            "goal": "write hello.txt containing namaste",
            "done": {"type": "file_contains", "path": "hello.txt", "text": "namaste"},
        }
    ]
}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 — http.server API
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])
        system = (messages[0].get("content") or "") if messages else ""

        if "Amatya" in system:
            request = json.loads(messages[-1]["content"])
            if request.get("mode") in ("PLAN", "REPLAN"):
                message = {"role": "assistant", "content": json.dumps(PLAN)}
            else:  # VERIFY
                message = {
                    "role": "assistant",
                    "content": json.dumps({"verdict": "pass", "reason": "artifact verified"}),
                }
        else:  # Karta
            if messages[-1]["role"] == "tool":
                message = {"role": "assistant", "content": "RESULT: wrote hello.txt with namaste"}
            else:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps({"path": "hello.txt", "content": "namaste"}),
                            },
                        }
                    ],
                }

        response = {
            "id": "mock-1",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "mock-spine"),
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
        }
        data = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args: object) -> None:  # silence request spam
        return


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8089
    print(f"mock gateway on http://127.0.0.1:{port}/v1", file=sys.stderr)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
