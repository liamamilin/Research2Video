import hashlib
import httpx
import pytest

from core.video.autovideo import AutoVideoClient, AutoVideoError


def artifact(data, **overrides):
    return {"name": "video.mp4", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "download_url": "/api/jobs/one/artifacts/video.mp4", **overrides}


def test_verified_existing_download_needs_no_network(tmp_path):
    data = b"already downloaded video"
    dest = tmp_path / "video.mp4"
    dest.write_bytes(data)
    def fail(request):
        pytest.fail("a verified artifact must be reused after an interrupted batch")
    AutoVideoClient("http://local.test", transport=httpx.MockTransport(fail)).download(artifact(data), dest)
    assert dest.read_bytes() == data


@pytest.mark.parametrize("existing", [b"partial", b"same length but corrupted"])
def test_stale_existing_download_is_replaced_after_verification(tmp_path, existing):
    data = b"correct completed video"
    dest = tmp_path / "video.mp4"
    dest.write_bytes(existing)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, content=data)
    AutoVideoClient("http://local.test", transport=httpx.MockTransport(handle)).download(artifact(data), dest)
    assert len(calls) == 1 and dest.read_bytes() == data
    assert not list(tmp_path.glob("*.part"))


def test_same_length_corruption_is_not_reused(tmp_path):
    data = b"correct completed video"
    dest = tmp_path / "video.mp4"
    dest.write_bytes(b"x" * len(data))
    client = AutoVideoClient("http://local.test", transport=httpx.MockTransport(lambda _: httpx.Response(200, content=data)))
    client.download(artifact(data), dest)
    assert dest.read_bytes() == data


@pytest.mark.parametrize("missing", ["sha256", "bytes"])
def test_missing_integrity_metadata_is_rejected_even_for_an_existing_file(tmp_path, missing):
    data = b"video"
    dest = tmp_path / "video.mp4"
    dest.write_bytes(data)
    metadata = artifact(data)
    del metadata[missing]
    with pytest.raises(AutoVideoError) as error:
        AutoVideoClient("http://local.test", transport=httpx.MockTransport(lambda _: pytest.fail("invalid metadata used the network"))).download(metadata, dest)
    assert error.value.code == "BAD_ARTIFACT_METADATA"


def test_failed_replacement_preserves_verified_old_output(tmp_path):
    dest = tmp_path / "video.mp4"
    dest.write_bytes(b"previous complete video")
    client = AutoVideoClient("http://local.test", transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"truncated")))
    with pytest.raises(AutoVideoError) as error:
        client.download(artifact(b"new complete video"), dest)
    assert error.value.code == "ARTIFACT_MISMATCH"
    assert dest.read_bytes() == b"previous complete video"
    assert not list(tmp_path.glob("*.part"))
