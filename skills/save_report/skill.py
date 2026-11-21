from pathlib import Path
from skill_context import filesystem


def run(input: dict) -> dict:
    destination = Path(filesystem[0]) / "report.txt"
    destination.write_text(input["report"], encoding="utf-8")
    return {"path": str(destination)}
