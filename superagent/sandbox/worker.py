"""Standalone stdlib-only worker, launched with -I -S and a clean environment."""
import contextlib
import io
import json
import os
import resource
import socket
import sys
import types
from pathlib import Path


def lock_process_creation():
    """Deny fork/exec at the kernel level inside the Linux namespace."""
    import ctypes
    import errno
    try:
        lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    except OSError as exc:
        raise RuntimeError("libseccomp.so.2 is required for Linux process isolation") from exc
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not ctx:
        raise RuntimeError("Cannot initialize seccomp")
    try:
        for name in (b"fork", b"vfork", b"clone", b"clone3", b"execve", b"execveat", b"ptrace", b"process_vm_readv", b"process_vm_writev", b"setsid", b"setpgid"):
            syscall = lib.seccomp_syscall_resolve_name(name)
            if syscall >= 0 and lib.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, syscall, 0) != 0:
                raise RuntimeError("Cannot install seccomp process rule")
        if lib.seccomp_load(ctx) != 0:
            raise RuntimeError("Cannot activate seccomp process isolation")
    finally:
        lib.seccomp_release(ctx)


def main():
    request = json.loads(sys.stdin.buffer.read(2_000_000))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (10_000_000, 10_000_000))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CPU, (request["cpu_seconds"], request["cpu_seconds"] + 1))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
        if not request["subprocess"]:
            lock_process_creation()
    paths = [Path(p).resolve() for p in request["paths"]]
    reads = [Path(sys.base_prefix).resolve(), Path(__file__).parent.resolve(), Path("/System/Library"), Path("/usr/lib")]
    endpoints = {tuple(item) for item in request["endpoints"]}
    source = Path(request["source"]).read_text()
    skill_context = types.ModuleType("skill_context")
    skill_context.filesystem = tuple(str(p) for p in paths)
    skill_context.network = tuple(endpoints)
    sys.modules["skill_context"] = skill_context

    def audit(event, args):
        if event == "open":
            raw, mode, flags = args
            if isinstance(raw, int):
                return
            path = Path(os.fsdecode(raw)).resolve()
            write = bool((flags or 0) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            allowed = paths if write else paths + reads
            if path != Path("/dev/null") and not any(path == p or path.is_relative_to(p) for p in allowed):
                raise PermissionError(f"Filesystem access denied by skill policy: {path}")
        elif event == "socket.connect":
            address = args[1]
            if address not in request["routes"].values():
                raise PermissionError(f"Network access denied by skill policy: {address}")
        elif event == "socket.sendto":
            # Network grants use TCP relays; datagrams have no permitted route.
            raise PermissionError("Datagram access denied by skill policy")
        elif event == "socket.getaddrinfo":
            host, port = args[0], args[1]
            if (host, int(port or 0)) not in endpoints:
                raise PermissionError("DNS denied: use the pre-resolved permitted endpoint")
        elif event in ("subprocess.Popen", "os.system", "os.fork", "os.posix_spawn", "os.exec"):
            if not request["subprocess"]:
                raise PermissionError("Subprocess access denied by skill policy")
        elif event.startswith("ctypes."):
            raise PermissionError("Native code loading denied by skill policy")

    # Map explicitly permitted DNS names to fixed addresses, without granting DNS egress.
    real_getaddrinfo = socket.getaddrinfo
    mappings = request["hosts"]

    def fixed_getaddrinfo(host, port, *args, **kwargs):
        target = mappings.get(f"{host}:{port}", host)
        return real_getaddrinfo(target, port, *args, **kwargs)

    socket.getaddrinfo = fixed_getaddrinfo
    original_socket = socket.socket

    class RoutedSocket(original_socket):
        def connect(self, address):
            if self.family not in (socket.AF_INET, socket.AF_INET6):
                return super().connect(address)
            host, port = address[:2]
            host = mappings.get(f"{host}:{port}", host)
            route = request["routes"].get(f"{host}:{port}")
            if not route or self.type != socket.SOCK_STREAM:
                raise PermissionError(f"Network access denied by skill policy: {address}")
            with original_socket(socket.AF_UNIX, socket.SOCK_STREAM) as relay:
                relay.settimeout(self.gettimeout())
                relay.connect(route)
                os.dup2(relay.fileno(), self.fileno())

        def connect_ex(self, address):
            try:
                self.connect(address)
                return 0
            except OSError as exc:
                return exc.errno or 1

    socket.socket = RoutedSocket
    sys.addaudithook(audit)

    class BoundedOutput(io.TextIOBase):
        def write(self, text):
            return len(text)

    scope = {"__name__": "__skill__", "__file__": request["source"]}
    with contextlib.redirect_stdout(BoundedOutput()), contextlib.redirect_stderr(BoundedOutput()):
        exec(compile(source, request["source"], "exec"), scope)
        result = scope["run"](request["input"])
    if not isinstance(result, dict):
        raise ValueError("run(input) must return a dict")
    output = json.dumps({"ok": True, "output": result}, ensure_ascii=False, allow_nan=False)
    if len(output.encode()) > 1_000_000:
        raise ValueError("Skill output exceeds 1 MB")
    sys.stdout.write(output)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)
