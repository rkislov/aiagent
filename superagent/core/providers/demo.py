"""Deterministic offline fixture, explicitly not a language model."""
import json
from uuid import uuid4


class DemoProvider:
    def chat(self, messages, tools):
        return next(e["message"] for e in self.stream(messages, tools) if e["type"] == "message")

    def stream(self, messages, tools):
        user_index = max(i for i, m in enumerate(messages) if m["role"] == "user")
        request = messages[user_index]["content"]
        results = [m for m in messages[user_index + 1:] if m["role"] == "tool"]
        names = {t["function"]["name"] for t in tools}
        if not results:
            if any(word in request.lower() for word in ("сохран", "save")) and "save_report" in names:
                name, payload = "save_report", {"report": request}
            elif "email_filter" in names:
                try:
                    parsed = json.loads(request)
                    payload = parsed if isinstance(parsed, dict) and "messages" in parsed else {"messages": [{"subject": request, "body": request}]}
                except ValueError:
                    payload = {"messages": [{"subject": request, "body": request}]}
                name = "email_filter"
            else:
                name, payload = None, None
        elif len(results) == 1 and results[0].get("name") == "email_filter" and "report_gen" in names:
            name, payload = "report_gen", {"classified": json.loads(results[0]["content"]).get("classified", [])}
        else:
            name, payload = None, None
        if name:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": f"call_{uuid4().hex}", "type": "function", "function": {"name": name, "arguments": json.dumps(payload, ensure_ascii=False)}}]}
        else:
            content = "Демонстрационный режим · без обращения к модели\n\n"
            if results:
                output = json.loads(results[-1]["content"])
                content += output.get("report", json.dumps(output, ensure_ascii=False, indent=2))
            else:
                content += "Добавьте навыки email_filter и report_gen для демонстрации обработки писем."
            for start in range(0, len(content), 24):
                yield {"type": "delta", "text": content[start:start + 24]}
            message = {"role": "assistant", "content": content}
        yield {"type": "message", "message": message}
