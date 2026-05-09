"""Tests for the Azure AI endpoint resolver.

The resolver is the single shared dependency every wrapper goes
through; if its caching or env-var contract regresses, every
service's request path silently breaks. These tests guard the
contract without requiring the real Azure SDK.
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock


def _install_fake_azure_modules() -> None:
    """Stub out azure.identity + azure.keyvault.secrets.

    The real SDKs aren't in the unit-test image; the resolver only
    needs them to be importable when its functions execute, so a
    minimal in-memory shim is enough.
    """
    azure_pkg = types.ModuleType("azure")
    azure_pkg.__path__ = []  # mark as package
    identity = types.ModuleType("azure.identity")

    class _FakeCredential:
        def __init__(self, **kw):
            self.kw = kw

        def get_token(self, scope):
            return types.SimpleNamespace(token=f"fake-{scope}")

    identity.DefaultAzureCredential = _FakeCredential

    kv_pkg = types.ModuleType("azure.keyvault")
    kv_pkg.__path__ = []
    secrets_mod = types.ModuleType("azure.keyvault.secrets")

    class _FakeSecret:
        def __init__(self, value):
            self.value = value

    class _FakeSecretClient:
        instances: list["_FakeSecretClient"] = []

        def __init__(self, vault_url, credential):
            self.vault_url = vault_url
            self.credential = credential
            self.calls: list[str] = []
            _FakeSecretClient.instances.append(self)

        def get_secret(self, name):
            self.calls.append(name)
            return _FakeSecret(f"https://{name}.example.cognitiveservices.azure.com/")

    secrets_mod.SecretClient = _FakeSecretClient

    sys.modules.setdefault("azure", azure_pkg)
    sys.modules["azure.identity"] = identity
    sys.modules.setdefault("azure.keyvault", kv_pkg)
    sys.modules["azure.keyvault.secrets"] = secrets_mod
    return _FakeSecretClient


class EndpointResolverTests(unittest.TestCase):
    def setUp(self):
        self._fake_client_cls = _install_fake_azure_modules()
        self._fake_client_cls.instances.clear()
        # Ensure a clean import per-test so the lru_cache is fresh.
        for name in list(sys.modules):
            if name.startswith("services.backend.azure_ai"):
                del sys.modules[name]
        # The package isn't importable as `services.backend.azure_ai`
        # in this repo layout (no top-level package), so import the
        # resolver directly by file path via importlib.
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "_resolver.py"
        spec = importlib.util.spec_from_file_location("azure_ai_resolver_under_test", path)
        self.resolver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.resolver)
        self.resolver.reset_for_tests()

    def tearDown(self):
        self.resolver.reset_for_tests()

    def test_endpoint_for_requires_kv_uri(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError) as cm:
                self.resolver.endpoint_for("openai")
        self.assertIn("AZURE_CRON_OBS_KV_URI", str(cm.exception))

    def test_endpoint_for_caches_per_feature(self):
        env = {"AZURE_CRON_OBS_KV_URI": "https://kv.vault.azure.net/"}
        with mock.patch.dict("os.environ", env, clear=True):
            url1 = self.resolver.endpoint_for("openai")
            url2 = self.resolver.endpoint_for("openai")
        self.assertEqual(url1, url2)
        # Two calls to endpoint_for("openai") should hit Key Vault once.
        all_calls = sum(len(c.calls) for c in self._fake_client_cls.instances)
        self.assertEqual(all_calls, 1)

    def test_endpoint_for_distinct_features(self):
        # Task #552 §G-R — Azure Speech + Translator retired; the
        # resolver only sees the surviving Azure surfaces now.
        env = {"AZURE_CRON_OBS_KV_URI": "https://kv.vault.azure.net/"}
        with mock.patch.dict("os.environ", env, clear=True):
            self.resolver.endpoint_for("openai")
            self.resolver.endpoint_for("document_intel")
            self.resolver.endpoint_for("vision")
        all_calls = sorted(
            name for c in self._fake_client_cls.instances for name in c.calls
        )
        self.assertEqual(
            all_calls,
            [
                "azure-ai-document_intel-endpoint",
                "azure-ai-openai-endpoint",
                "azure-ai-vision-endpoint",
            ],
        )

    def test_get_credential_is_singleton(self):
        c1 = self.resolver.get_credential()
        c2 = self.resolver.get_credential()
        self.assertIs(c1, c2)


if __name__ == "__main__":
    unittest.main()
