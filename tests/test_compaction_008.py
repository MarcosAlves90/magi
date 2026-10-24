"""MAGI-008 preventive stale-preview feedback (backend remains authoritative)."""

from pathlib import Path

import pytest

from test_desktop import FakeMemoryStore, client_for, load_api


@pytest.mark.parametrize("target", ["memory", "user"])
def test_magi_008_store_response_fingerprint_tracks_real_entries(monkeypatch, target):
    api = load_api()
    store = FakeMemoryStore(memory=["source one"], user=["person one"])
    monkeypatch.setattr(api, "_memory_store", lambda: store)
    client = client_for(api)
    first = client.get("/memory").json()["targets"][target]
    assert first["source_fingerprint"] == api._source_fingerprint(getattr(store, target + "_entries"))
    getattr(store, target + "_entries").append("outside edit")
    second = client.get("/memory").json()["targets"][target]
    assert first["source_fingerprint"] != second["source_fingerprint"]
    failed = client.post(f"/memory/{target}/compact", json={"source_fingerprint": first["source_fingerprint"], "entries": ["short"]})
    assert failed.status_code == 409
    assert store.batch_calls == []


def test_magi_008_prevent_stale_apply_in_desktop_without_background_ai():
    source = (Path(__file__).resolve().parents[1] / "desktop/plugin.js").read_text()
    stored = source.split("function StoredMemoryPage", 1)[1].split("function MagiPage", 1)[0]
    assert "isPreviewStale" in stored
    assert "source_fingerprint" in stored
    assert "Preview outdated" in stored
    assert "Create a new preview" in stored
    assert "disabled: applyingCompaction || isPreviewStale" in stored
    assert "if (isPreviewStale) return" in stored
    assert "onClick: compact" in stored
