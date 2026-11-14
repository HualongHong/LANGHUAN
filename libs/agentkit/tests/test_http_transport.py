"""Redirect policy shared by authenticated stdlib HTTP clients."""

from __future__ import annotations

import importlib

import pytest


class _RedirectResponse:
    status = 302
    headers = {"Location": "https://unexpected.example/landing"}
    body = b""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.body


class _RecordingOpener:
    def __init__(self):
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, timeout))
        return _RedirectResponse()


@pytest.mark.parametrize("module_name", ["agentkit.mineru", "agentkit.dianshi", "agentkit.sciverse"])
def test_default_transport_surfaces_redirect_without_following(module_name, monkeypatch):
    module = importlib.import_module(module_name)
    opener = _RecordingOpener()
    monkeypatch.setattr(module, "_OPENER", opener)
    response = module.default_transport(
        "GET",
        "https://api.example/resource",
        {"Authorization": "Bearer not-forwarded"},
        None,
        3.0,
    )
    assert response.status == 302
    assert response.headers["Location"] == "https://unexpected.example/landing"
    assert opener.calls == [("https://api.example/resource", 3.0)]
