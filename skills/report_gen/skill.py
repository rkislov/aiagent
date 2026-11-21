def run(input: dict) -> dict:
    messages = input["classified"]
    counts = {}
    for message in messages:
        category = str(message.get("category", "Общее"))
        counts[category] = counts.get(category, 0) + 1
    lines = ["Отчёт по входящим письмам", "", f"Обработано писем: {len(messages)}", ""]
    for category, count in sorted(counts.items()):
        lines.append(f"• {category}: {count}")
    for message in messages:
        lines.extend(["", f"{message.get('subject', 'Без темы')} · {message.get('category', 'Общее')}",
                      f"Приоритет: {message.get('priority', 'Обычный')}", f"Черновик: {message.get('draft', '')}"])
    return {"report": "\n".join(lines)}
