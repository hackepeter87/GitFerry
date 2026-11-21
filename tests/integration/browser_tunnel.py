"""Host browser ingress without adding egress to the isolated importer network.

Each local connection reaches only localhost:8080 inside gitferry-b via Docker exec.
The application receives ordinary HTTP. No arbitrary destination, source workspace,
credential arguments or additional Docker network is exposed to the importer.
"""

from __future__ import annotations

import select
import socketserver
import subprocess
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

_REMOTE = """
import os, select, socket
with socket.create_connection(('127.0.0.1', 8080), timeout=10) as peer:
    peer.settimeout(None)
    while True:
        readable, _, _ = select.select([0, peer], [], [], 180)
        if not readable:
            break
        if 0 in readable:
            data = os.read(0, 65536)
            if not data:
                break
            peer.sendall(data)
        if peer in readable:
            data = peer.recv(65536)
            if not data:
                break
            view = memoryview(data)
            while view:
                view = view[os.write(1, view):]
"""


@contextmanager
def browser_tunnel(compose: Sequence[str], service: str = "gitferry-b") -> Iterator[str]:
    stopping = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            process = subprocess.Popen(  # noqa: S603 -- fixed target in the test's Compose project
                [*compose, "exec", "-T", service, "python", "-u", "-c", _REMOTE],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            assert process.stdin is not None and process.stdout is not None  # noqa: S101 -- PIPE invariant
            deadline = time.monotonic() + 180
            self.request.settimeout(10)
            try:
                while not stopping.is_set() and time.monotonic() < deadline:
                    readable, _, _ = select.select([self.request, process.stdout], [], [], 0.5)
                    for source in readable:
                        data = (
                            self.request.recv(65536)
                            if source is self.request
                            else process.stdout.read(65536)
                        )
                        if not data:
                            return
                        if source is self.request:
                            view = memoryview(data)
                            while view:
                                written = process.stdin.write(view)
                                if not written:
                                    return
                                view = view[written:]
                        else:
                            self.request.sendall(data)
            except OSError:
                # Browser disconnects close only their own forwarding process.
                pass
            finally:
                process.stdin.close()
                process.stdout.close()
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            stopping.set()
            server.shutdown()
            thread.join(timeout=5)
