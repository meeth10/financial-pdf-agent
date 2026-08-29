"""The agent loop.

Deliberately not the fully-autonomous OpenAI-Agents-SDK style loop —
see the guide's own advice to start with a single agent and a plain
while-loop before reaching for orchestration frameworks. This is that
loop, aimed at a local Ollama endpoint instead of the OpenAI API.

Cheapest-path-first: if the question maps cleanly onto a single
deterministic lookup, you don't need this file at all — call
tools.get_line_item() directly from a CLI or notebook. Reserve this
loop for genuinely ambiguous questions (unclear metric name, missing
period, needs cross-checking, needs a derived calculation, or needs a
narrative answer synthesized from more than one lookup).
"""

import json
import sqlite3

from ollama import Client

from .system_prompt import SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS, DISPATCH

DEFAULT_MODEL = "hermes4-14b"
MAX_TURNS = 8  # stop condition — see the guide's "when to stop" rule


def ask(conn: sqlite3.Connection, question: str, model: str = DEFAULT_MODEL,
        host: str = "http://localhost:11434") -> str:
    client = Client(host=host)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    for _ in range(MAX_TURNS):
        response = client.chat(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            options={"temperature": 0.1},
        )
        message = response["message"]
        messages.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            content = message.get("content", "")
            # strip Hermes 4's hybrid-mode <think> block if present
            if "<think>" in content and "</think>" in content:
                content = content.split("</think>", 1)[1].strip()
            return content

        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            fn = DISPATCH.get(name)
            if fn is None:
                result = {"status": "error", "message": f"unknown tool {name}"}
            else:
                result = fn(conn, **args)
            messages.append({
                "role": "tool",
                "content": json.dumps(result),
            })

    return "Stopped after max turns without a final answer — see message history for the last state."


if __name__ == "__main__":
    import sys
    from store.schema import init_db

    if len(sys.argv) < 3:
        print("usage: python -m agent.run <db_path> <question>")
        sys.exit(1)

    conn = init_db(sys.argv[1])
    print(ask(conn, " ".join(sys.argv[2:])))
