---
name: save_report
description: Записывает текст отчёта в data/workspace/report.txt. Требует явного подтверждения.
version: 0.1.0
inputs:
  report: str
outputs:
  path: str
irreversible: true
permissions:
  filesystem: [./data/workspace]
  network: []
  subprocess: false
---

Демонстрация подтверждения записи на диск. Каталог должен существовать заранее.
