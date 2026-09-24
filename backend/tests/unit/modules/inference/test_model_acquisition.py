"""Download admission rejects unsafe URLs before a client sees them."""

import pytest
from printstash_core.inference import EmbeddingError

from app.modules.inference.model_acquisition import DownloadPolicy


class TestDownloadPolicy:
    @pytest.mark.parametrize(
        "url",
        [
            "http://huggingface.co/model",
            "https://huggingface.co.evil.test/model",
            "https://user:secret@huggingface.co/model",
            "https://huggingface.co:8443/model",
            "https://huggingface.co/model#fragment",
            "https://huggingface.co/model\nsecret",
            "https://127.0.0.1/model",
            "file:///private/model",
            "https://huggingface.co:" + "9" * 20,
        ],
    )
    def test_rejects_hostile_model_download_urls(self, url):
        with pytest.raises(EmbeddingError, match="download_url_forbidden"):
            DownloadPolicy().validate(url)

    def test_accepts_an_explicit_https_mirror(self):
        policy = DownloadPolicy("https://mirror.test:9443/models")
        assert (
            policy.validate("https://mirror.test:9443/object")
            == "https://mirror.test:9443/object"
        )
        assert policy.validate("https://cas-bridge.xethub.hf.co/object?signature=test")
        with pytest.raises(EmbeddingError):
            policy.validate("https://mirror.test:9444/object")

    @pytest.mark.parametrize(
        "mirror",
        [
            "http://mirror.test",
            "https://user:secret@mirror.test",
            "https://mirror.test/?token=secret",
        ],
    )
    def test_rejects_invalid_mirror_configuration(self, mirror):
        with pytest.raises(EmbeddingError, match="download_url_forbidden"):
            DownloadPolicy(mirror).validate("https://huggingface.co/model")
