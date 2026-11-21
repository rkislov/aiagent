---
name: email_filter
description: Классифицирует переданные письма и готовит черновики. Не подключается к почтовому ящику.
version: 0.1.0
inputs:
  messages: list[dict]
outputs:
  classified: list[dict]
permissions:
  filesystem: []
  network: []
  subprocess: false
---

Локальный пример классификации по ключевым словам. Все данные поступают во входном JSON.
