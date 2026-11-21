"""Fixed-destination Unix socket relays keep host allowlists outside skill code.

The child OS policy grants only these socket paths, never raw IP networking.
"""
from __future__ import annotations

import select
import socket
import threading
import time
from pathlib import Path


class NetworkRelays:
    def __init__(self, stage: Path, endpoints: list[tuple[str, int]], timeout: float):
        self.stage, self.endpoints, self.timeout = stage, endpoints, timeout
        self.routes = {}
        self.listeners = []
        self.connections = set()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.slots = threading.BoundedSemaphore(16)

    def __enter__(self):
        try:
            for index, endpoint in enumerate(self.endpoints):
                path = str(self.stage / f"n{index}.sock")
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                listener.bind(path)
                listener.listen(8)
                listener.settimeout(.25)
                self.listeners.append(listener)
                self.routes[f"{endpoint[0]}:{endpoint[1]}"] = path
                threading.Thread(target=self.accept, args=(listener, endpoint), daemon=True).start()
            return self.routes
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def accept(self, listener, endpoint):
        while not self.stop.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self.slots.acquire(blocking=False):
                connection.close()
                continue
            threading.Thread(target=self.forward, args=(connection, endpoint), daemon=True).start()

    def forward(self, local, endpoint):
        remote = None
        try:
            remote = socket.create_connection(endpoint, timeout=min(self.timeout, 10))
            with self.lock:
                self.connections.update((local, remote))
            local.setblocking(False)
            remote.setblocking(False)
            deadline = time.monotonic() + self.timeout
            while not self.stop.is_set() and time.monotonic() < deadline:
                ready, _, _ = select.select([local, remote], [], [], .25)
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    destination = remote if source is local else local
                    # Bounded blocking writes provide backpressure without unbounded buffers.
                    destination.settimeout(min(self.timeout, 2))
                    destination.sendall(data)
                    destination.setblocking(False)
        except OSError:
            pass
        finally:
            with self.lock:
                self.connections.discard(local)
                self.connections.discard(remote)
            local.close()
            if remote:
                remote.close()
            self.slots.release()

    def __exit__(self, *args):
        self.stop.set()
        for listener in self.listeners:
            listener.close()
        with self.lock:
            for connection in self.connections:
                connection.close()
