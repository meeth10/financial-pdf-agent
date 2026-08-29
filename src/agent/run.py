"""Ollama tool-calling agent over the structured financial store."""

import argparse
import json
import sqlite3

from ollama import Client

from .system_prompt import SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS, DISPATCH

DEFAULT_MODEL = "hermes3:8b"
MAX_TURNS = 8


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
            if "<think>" in content and "</think>" in content:
                content = content.split("</think>", 1)[1].strip()
            return content

        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            fn = DISPATCH.get(name)
            if fn is None:
                result = {"status": "error", "message": f"unknown tool {name}"}
            else:
                result = fn(conn, **args)
            messages.append({"role": "tool", "content": json.dumps(result)})

    return "Stopped after max turns without a final answer."


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("question", nargs="+")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    from src.store.schema import init_db
    conn = init_db(args.db_path)
    print(ask(conn, " ".join(args.question), model=args.model))
