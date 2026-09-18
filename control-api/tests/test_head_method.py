"""HEAD must succeed on public HTML pages (Firefox / probes)."""

from __future__ import annotations


def test_cloud_register_accepts_head(client):
    response = client.head("/cloud/register?plan=trial&cycle=monthly&lang=ar")
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("text/html")
    assert response.content == b""


def test_home_accepts_head(client):
    response = client.head("/")
    assert response.status_code == 200
    assert response.content == b""
