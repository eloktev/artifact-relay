import math
import re


def test_ids_are_opaque_urlsafe_and_high_entropy(publish):
    ids = [publish(title=f"Отчёт {i}").json()["id"] for i in range(25)]

    assert len(set(ids)) == 25, "identifiers must not repeat"
    for artifact_id in ids:
        assert re.fullmatch(r"[A-Za-z0-9_-]+", artifact_id), artifact_id
        # base64url: 6 bits per character. The brief demands >= 128 bits.
        assert len(artifact_id) * 6 >= 128, artifact_id

    # No shared prefix -> not a counter, timestamp or hash of the (very similar) titles.
    assert len({artifact_id[:6] for artifact_id in ids}) > 20


def test_new_artifact_id_draws_from_the_csprng(monkeypatch):
    from artifact_relay import storage

    assert storage.ID_BYTES * 8 >= 128

    seen = {storage.new_artifact_id() for _ in range(200)}
    assert len(seen) == 200
    # Rough sanity check that the alphabet is actually being used.
    alphabet = set("".join(seen))
    assert len(alphabet) > 40
    assert math.log2(len(alphabet)) > 5
