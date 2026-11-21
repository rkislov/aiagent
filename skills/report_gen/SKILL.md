---
name: report_gen
description: Создаёт текстовый отчёт по классифицированным письмам без записи на диск.
version: 0.1.0
inputs:
  classified: list[dict]
outputs:
  report: str
permissions:
  filesystem: []
  network: []
  subprocess: false
---

Возвращает готовый отчёт в поле report.
