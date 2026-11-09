"""Production entry point: listen on BOTH IPv4 and IPv6 on $PORT.

Some platforms (e.g. Railway private networking) reach services over IPv6, others over IPv4. A single `--host ::`
socket is IPv6-only, so two sockets are opened. If IPv6 is unavailable (plain Docker), only IPv4 is used.
Run with:  python -m app.serve
"""
import os
import socket

import uvicorn


def _sockets(port: int) -> list[socket.socket]:
    socks = []
    s4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s4.bind(("0.0.0.0", port))
    socks.append(s4)
    try:
        s6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        s6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        s6.bind(("::", port))
        socks.append(s6)
    except OSError:
        pass  # no IPv6 in this environment
    return socks


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    server = uvicorn.Server(uvicorn.Config("app.main:app", log_level="info", proxy_headers=True, forwarded_allow_ips="*"))
    server.run(sockets=_sockets(port))


if __name__ == "__main__":
    main()
