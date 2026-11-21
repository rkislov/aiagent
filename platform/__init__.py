"""Repository-only CLI compatibility without breaking Python's stdlib platform.

The installed distribution uses superagent; this shim is deliberately not packaged.
"""
import sysconfig as _sysconfig
from pathlib import Path as _Path

_stdlib = _Path(_sysconfig.get_path("stdlib")) / "platform.py"
exec(compile(_stdlib.read_bytes(), str(_stdlib), "exec"), globals())
