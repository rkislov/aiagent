def run(input: dict) -> dict:
    classified = []
    for message in input["messages"]:
        subject = str(message.get("subject", "Без темы"))
        text = (subject + " " + str(message.get("body", ""))).lower()
        if any(word in text for word in ("счёт", "счет", "invoice", "оплат")):
            category = "Финансы"
        elif any(word in text for word in ("купить", "стоимость", "лид", "цена", "pricing", "demo")):
            category = "Продажи"
        else:
            category = "Общее"
        classified.append({"subject": subject, "category": category,
                           "priority": "Высокий" if any(word in text for word in ("срочно", "urgent")) else "Обычный",
                           "draft": "Спасибо за письмо! Мы получили ваш запрос и вернёмся с ответом."})
    return {"classified": classified}
