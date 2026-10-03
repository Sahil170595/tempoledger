"""Offline tests never use credentials, service sockets or local dotenv files."""

import socket
import threading

import pytest


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    internal = threading.local()

    def socketpair(*args, **kwargs):
        # Windows implements the event loop's wakeup pipe as a local socketpair.
        internal.socketpair = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            internal.socketpair = False

    def denied(*args, **kwargs):
        raise AssertionError("Network access is forbidden in the offline test suite")

    def connect(sock, address):
        if getattr(internal, "socketpair", False):
            return original_connect(sock, address)
        return denied()

    monkeypatch.setattr(socket, "socketpair", socketpair)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)

    from tempoledger.config import settings

    monkeypatch.setattr(settings, "enable_live_delivery", False)
    monkeypatch.setattr(settings, "enable_live_agent", False)
    monkeypatch.setattr(settings, "otel_exporter_otlp_endpoint", None)
