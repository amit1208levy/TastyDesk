"""Run the server: the dashboard's listener, and the phone's when one is paired.

One process and one engine behind both, so the phone and the Mac can never
disagree and the broker is synced once rather than twice. Both sockets bind
to 127.0.0.1; see :mod:`tastydesk.api.phone` for how they differ.
"""

from __future__ import annotations

import logging
import socket

import uvicorn

from tastydesk.api.main import app
from tastydesk.api.phone import PhoneGate, paired_digest, phone_port

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"


def _listen(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, port))
    sock.set_inheritable(True)
    return sock


def serve(port: int) -> None:
    digest = paired_digest()
    sockets = [_listen(port)]
    gate_port: int | None = None
    if digest:
        gate_port = phone_port()
        sockets.append(_listen(gate_port))
        print(f"Phone listener on {HOST}:{gate_port} (paired; token required)")
    else:
        print("No phone paired — run ./scripts/setup-phone.sh to add one.")

    config = uvicorn.Config(PhoneGate(app, gate_port, digest), host=HOST, port=port)
    uvicorn.Server(config).run(sockets=sockets)
