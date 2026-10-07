"""Phase 14 tests: offline Ed25519 license issuance and verification.

Covers the License model (plans, gating, expiry), the wire format
(payload.signature, urlsafe base64), signing/verification roundtrip with
generated key pairs, and rejection of tampered / expired / malformed keys.
No repo key material is required — all keys are generated in-memory.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from unittest import mock

from licensing import (
    FREE,
    PRO,
    PRO_FEATURES,
    License,
    LicenseError,
    ProRequiredError,
    activate,
    clear_activation,
    current_license,
    decode_key,
    encode_key,
    ensure_pro,
    feature_enabled,
    issue_license,
    load_activation,
    parse_datetime,
    verify_license,
)
from licensing.issuer import load_private_key, main as issuer_main
from licensing.verifier import PUBLIC_KEY_B64, load_public_key


@pytest.fixture()
def keypair():
    private = Ed25519PrivateKey.generate()
    public_b64 = base64.b64encode(
        private.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).decode("ascii")
    return private, public_b64


def issue(email="user@example.com", plan=PRO, expires_at=None, private=None):
    license_obj, key = issue_license(
        email, plan=plan, expires_at=expires_at, private_key=private
    )
    return license_obj, key


# ═══════════════════════════════════════════════════════════════════════════════
#  License model
# ═══════════════════════════════════════════════════════════════════════════════

class TestLicenseModel:
    def test_rejects_unknown_plan(self):
        with pytest.raises(LicenseError, match="unknown plan"):
            License(email="a@b.c", plan="enterprise")

    def test_rejects_empty_email(self):
        with pytest.raises(LicenseError):
            License(email="   ", plan=FREE)

    def test_free_plan_is_not_pro_and_gates_pro_features(self):
        lic = License(email="a@b.c", plan=FREE)
        assert not lic.is_pro
        for feature in PRO_FEATURES:
            assert not lic.feature_enabled(feature)

    def test_pro_plan_enables_pro_features(self):
        lic = License(email="a@b.c", plan=PRO)
        assert lic.is_pro
        for feature in PRO_FEATURES:
            assert lic.feature_enabled(feature)

    def test_unknown_feature_is_always_enabled(self):
        free = License(email="a@b.c", plan=FREE)
        assert free.feature_enabled("chat")
        assert free.feature_enabled("rag")

    def test_perpetual_license_never_expires(self):
        lic = License(email="a@b.c", plan=PRO, expires_at=None)
        assert not lic.is_expired
        assert lic.is_pro

    def test_naive_datetimes_normalized_to_utc(self):
        naive = datetime(2030, 1, 1, 12, 0, 0)
        lic = License(email="a@b.c", plan=PRO, expires_at=naive, issued_at=naive)
        assert lic.expires_at.tzinfo is not None
        assert lic.issued_at.tzinfo is not None

    def test_payload_roundtrip_preserves_fields(self):
        lic = License(
            email="a@b.c",
            plan=PRO,
            expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
            issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        restored = License.from_payload(lic.to_payload())
        assert restored == lic

    def test_from_payload_rejects_non_object(self):
        with pytest.raises(LicenseError, match="JSON object"):
            License.from_payload(["not", "a", "dict"])

    def test_from_payload_rejects_invalid_datetime(self):
        with pytest.raises(LicenseError, match="invalid datetime"):
            License.from_payload({"email": "a@b.c", "plan": PRO, "expires": "soon"})


class TestParseDatetime:
    def test_parses_z_suffix(self):
        parsed = parse_datetime("2030-01-01T00:00:00Z")
        assert parsed.tzinfo is not None

    def test_parses_naive_as_utc(self):
        parsed = parse_datetime("2030-01-01T00:00:00")
        assert parsed.tzinfo is not None

    @pytest.mark.parametrize("bad", ["", "not-a-date", 42, None])
    def test_rejects_invalid(self, bad):
        with pytest.raises(LicenseError):
            parse_datetime(bad)


# ═══════════════════════════════════════════════════════════════════════════════
#  Wire format
# ═══════════════════════════════════════════════════════════════════════════════

class TestWireFormat:
    def test_encode_decode_roundtrip(self):
        encoded = encode_key(b"payload-bytes", b"sig-bytes")
        assert decode_key(encoded) == (b"payload-bytes", b"sig-bytes")

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "nodot",
            "onlypayload.",
            ".onlysignature",
            "a.b.c",
            "!!!.???",
            None,
            12345,
        ],
    )
    def test_decode_rejects_malformed_keys(self, bad):
        with pytest.raises(LicenseError):
            decode_key(bad)

    def test_decode_strips_whitespace(self):
        encoded = encode_key(b"p", b"s")
        assert decode_key(f"  {encoded}  ") == (b"p", b"s")


# ═══════════════════════════════════════════════════════════════════════════════
#  Issue / verify
# ═══════════════════════════════════════════════════════════════════════════════

class TestIssueVerify:
    def test_perpetual_roundtrip(self, keypair):
        private, public_b64 = keypair
        _, key = issue("djonros@gmail.com", private=private)
        verified = verify_license(key, public_b64)
        assert verified.email == "djonros@gmail.com"
        assert verified.plan == PRO
        assert verified.is_pro
        assert not verified.is_expired
        assert verified.issued_at is not None
        assert verified.key_id
        assert verified.device is None

    def test_every_key_has_unique_key_id(self, keypair):
        private, _ = keypair
        kids = set()
        for _ in range(20):
            _, key = issue("a@b.c", private=private)
            payload_bytes, _ = decode_key(key)
            kids.add(json.loads(payload_bytes)["kid"])
        assert len(kids) == 20

    def test_device_bound_key_roundtrip(self, keypair, monkeypatch):
        private, public_b64 = keypair
        from licensing import fingerprint

        code = "AB12-CD34-EF56-7890"
        monkeypatch.setattr(fingerprint, "device_code", lambda: code)
        _, key = issue_license("a@b.c", private_key=private, device=code)
        verified = verify_license(key, public_b64)
        assert verified.device == "AB12CD34EF567890"
        assert verified.is_pro

    def test_device_bound_key_rejected_on_other_machine(self, keypair, monkeypatch):
        private, public_b64 = keypair
        from licensing import fingerprint

        monkeypatch.setattr(fingerprint, "device_code", lambda: "0000-0000-0000-0000")
        _, key = issue_license("a@b.c", private_key=private, device="AB12-CD34-EF56-7890")
        monkeypatch.setattr(fingerprint, "device_code", lambda: "1111-1111-1111-1111")
        with pytest.raises(LicenseError, match="another computer"):
            verify_license(key, public_b64)

    def test_device_normalization_accepts_loose_input(self, keypair, monkeypatch):
        private, public_b64 = keypair
        from licensing import fingerprint

        monkeypatch.setattr(fingerprint, "device_code", lambda: "ab12 cd34-ef56 7890")
        _, key = issue_license("a@b.c", private_key=private, device="AB12CD34EF567890")
        verified = verify_license(key, public_b64)
        assert verified.is_pro

    def test_timed_roundtrip(self, keypair):
        private, public_b64 = keypair
        expires = datetime.now(timezone.utc) + timedelta(days=365)
        _, key = issue("a@b.c", expires_at=expires, private=private)
        verified = verify_license(key, public_b64)
        assert verified.expires_at is not None
        assert abs((verified.expires_at - expires).total_seconds()) < 1
        assert verified.is_pro

    def test_payload_is_compact_sorted_json(self, keypair):
        private, _ = keypair
        _, key = issue("a@b.c", private=private)
        payload_bytes, _ = decode_key(key)
        payload = json.loads(payload_bytes)
        assert set(payload) == {"email", "plan", "issued_at", "kid"}
        assert isinstance(payload["kid"], str) and len(payload["kid"]) == 16

    def test_tampered_signature_rejected(self, keypair):
        private, public_b64 = keypair
        _, key = issue("a@b.c", private=private)
        tampered = key[:-2] + ("aa" if not key.endswith("aa") else "bb")
        with pytest.raises(LicenseError, match="signature"):
            verify_license(tampered, public_b64)

    def test_tampered_payload_rejected(self, keypair):
        private, public_b64 = keypair
        _, key = issue("a@b.c", private=private)
        raw_payload = json.dumps(
            {"email": "attacker@evil.com", "plan": PRO},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        forged = encode_key(raw_payload, decode_key(key)[1])
        with pytest.raises(LicenseError, match="signature"):
            verify_license(forged, public_b64)

    def test_expired_license_rejected(self, keypair):
        private, public_b64 = keypair
        expired = datetime.now(timezone.utc) - timedelta(days=1)
        _, key = issue("a@b.c", expires_at=expired, private=private)
        with pytest.raises(LicenseError, match="expired"):
            verify_license(key, public_b64)

    def test_key_signed_by_other_key_rejected(self, keypair):
        private, _ = keypair
        other = Ed25519PrivateKey.generate()
        other_b64 = base64.b64encode(
            other.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode("ascii")
        _, key = issue("a@b.c", private=private)
        with pytest.raises(LicenseError, match="signature"):
            verify_license(key, other_b64)

    def test_payload_with_unknown_plan_rejected(self, keypair):
        private, public_b64 = keypair
        raw_payload = json.dumps(
            {"email": "a@b.c", "plan": "enterprise"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        forged = encode_key(raw_payload, private.sign(raw_payload))
        with pytest.raises(LicenseError, match="unknown plan"):
            verify_license(forged, public_b64)

    def test_non_json_payload_rejected(self, keypair):
        private, public_b64 = keypair
        raw = b"not-json-at-all"
        forged = encode_key(raw, private.sign(raw))
        with pytest.raises(LicenseError, match="JSON"):
            verify_license(forged, public_b64)


# ═══════════════════════════════════════════════════════════════════════════════
#  Embedded / dev public key and issuer key loading
# ═══════════════════════════════════════════════════════════════════════════════

class TestKeys:
    def test_embedded_public_key_loads(self):
        key = load_public_key(PUBLIC_KEY_B64)
        assert key.__class__.__name__ == "Ed25519PublicKey"

    def test_invalid_public_key_rejected(self):
        with pytest.raises(LicenseError, match="public key"):
            load_public_key("!!!not-base64!!!")

    def test_load_private_key_missing_file(self, tmp_path):
        with pytest.raises(LicenseError, match="cannot load private key"):
            load_private_key(tmp_path / "missing.pem")

    def test_load_private_key_rejects_non_ed25519(self, tmp_path):
        from cryptography.hazmat.primitives.asymmetric import rsa

        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        path = tmp_path / "rsa.pem"
        path.write_bytes(
            rsa_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        with pytest.raises(LicenseError, match="not an Ed25519"):
            load_private_key(path)

    def test_dev_keypair_roundtrip_if_present(self):
        """The repo dev keypair (keys/, gitignored) signs keys the embedded
        public key accepts. Skipped when keys/private.pem is absent (e.g. CI)."""
        path = __import__("pathlib").Path(__file__).resolve().parents[1] / "keys" / "private.pem"
        if not path.exists():
            pytest.skip("dev keys/private.pem not present")
        _, key = issue("dev@example.com", private=load_private_key(path))
        verified = verify_license(key)
        assert verified.email == "dev@example.com"


# ═══════════════════════════════════════════════════════════════════════════════
#  Local activation store
# ═══════════════════════════════════════════════════════════════════════════════

class TestStore:
    def _key(self, keypair, email="stored@example.com", **kwargs):
        private, public_b64 = keypair
        _, key = issue(email, private=private, **kwargs)
        return key

    @pytest.fixture()
    def patched_public_key(self, keypair, monkeypatch):
        import licensing.verifier

        _, public_b64 = keypair
        monkeypatch.setattr(licensing.verifier, "PUBLIC_KEY_B64", public_b64)

    def test_activate_persists_and_loads(self, keypair, tmp_path, monkeypatch, patched_public_key):
        from licensing import store

        monkeypatch.setattr(store, "DEFAULT_LICENSE_PATH", tmp_path / "license.key")
        key = self._key(keypair)
        lic = activate(key)
        assert lic.plan == PRO
        stored = load_activation()
        assert stored is not None and stored.email == "stored@example.com"

    def test_activate_rejects_invalid_key_without_writing(self, keypair, tmp_path, monkeypatch):
        from licensing import store

        path = tmp_path / "license.key"
        monkeypatch.setattr(store, "DEFAULT_LICENSE_PATH", path)
        with pytest.raises(LicenseError):
            activate("garbage-key")
        assert not path.exists()
        assert load_activation() is None

    def test_load_missing_file_returns_none(self, tmp_path, monkeypatch):
        from licensing import store

        monkeypatch.setattr(store, "DEFAULT_LICENSE_PATH", tmp_path / "license.key")
        assert load_activation() is None

    def test_load_tampered_file_returns_none(self, keypair, tmp_path, monkeypatch, patched_public_key):
        from licensing import store

        path = tmp_path / "license.key"
        monkeypatch.setattr(store, "DEFAULT_LICENSE_PATH", path)
        key = self._key(keypair)
        path.write_text(key[:-2] + "zz\n", encoding="utf-8")
        assert load_activation() is None

    def test_clear_activation(self, keypair, tmp_path, monkeypatch, patched_public_key):
        from licensing import store

        monkeypatch.setattr(store, "DEFAULT_LICENSE_PATH", tmp_path / "license.key")
        activate(self._key(keypair))
        assert clear_activation() is True
        assert load_activation() is None
        assert clear_activation() is False

    def test_activate_creates_parent_directory(self, keypair, tmp_path, patched_public_key):
        key = self._key(keypair)
        lic = activate(key, path=tmp_path / "nested" / "dir" / "license.key")
        assert lic.is_pro

    def test_license_file_env_override(self, tmp_path, monkeypatch):
        from licensing import store

        target = tmp_path / "override" / "license.key"
        monkeypatch.setenv("FLUXION_LICENSE_FILE", str(target))
        assert store._default_license_path() == target

    def test_appdata_fallback_when_local_key_missing(self, tmp_path, monkeypatch):
        from licensing import store

        appdata = tmp_path / "appdata"
        shared = appdata / "Fluxion" / "license.key"
        shared.parent.mkdir(parents=True)
        shared.write_text("x\n", encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(appdata))
        monkeypatch.setattr(
            store, "__file__", str(tmp_path / "bundle" / "licensing" / "store.py")
        )
        assert store._default_license_path() == shared

    def test_local_key_preferred_over_appdata(self, tmp_path, monkeypatch):
        from licensing import store

        appdata = tmp_path / "appdata"
        shared = appdata / "Fluxion" / "license.key"
        shared.parent.mkdir(parents=True)
        shared.write_text("x\n", encoding="utf-8")
        local = tmp_path / "bundle" / "data" / "license.key"
        local.parent.mkdir(parents=True)
        local.write_text("y\n", encoding="utf-8")
        monkeypatch.setenv("APPDATA", str(appdata))
        monkeypatch.setattr(
            store, "__file__", str(tmp_path / "bundle" / "licensing" / "store.py")
        )
        assert store._default_license_path() == local


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI /license command
# ═══════════════════════════════════════════════════════════════════════════════

class TestLicenseCommand:
    @pytest.fixture()
    def registry(self, keypair, tmp_path, monkeypatch):
        import licensing.store
        import licensing.verifier

        monkeypatch.setattr(licensing.store, "DEFAULT_LICENSE_PATH", tmp_path / "license.key")
        _, public_b64 = keypair
        monkeypatch.setattr(licensing.verifier, "PUBLIC_KEY_B64", public_b64)

        class FakeRenderer:
            def __init__(self):
                self.messages = []

            def _log(self, kind, text):
                self.messages.append((kind, text))

            def info(self, text):
                self._log("info", text)

            def success(self, text):
                self._log("success", text)

            def error(self, text):
                self._log("error", text)

            def warn(self, text):
                self._log("warn", text)

        class FakeREPL:
            renderer = FakeRenderer()

        class FakeSettings:
            rag = None
            web = None
            ollama_host = "http://localhost:11434"

        class FakeBackend:
            pass

        from unittest import mock

        import cli.app as cli_app

        with mock.patch.multiple(
            cli_app,
            RAGConfig=mock.MagicMock(),
            RAGService=mock.MagicMock(),
            WebSearch=mock.MagicMock(),
            Assistant=mock.MagicMock(),
        ):
            registry = cli_app.build_registry(FakeREPL(), FakeBackend(), FakeSettings())
        return registry, FakeREPL.renderer

    def _run(self, registry, renderer, *args):
        cmd = registry.get("license")
        assert cmd is not None
        cmd.handler(list(args))
        return [text for _, text in renderer.messages]

    def test_registered_in_help(self, registry):
        reg, _ = registry
        assert "license" in reg.names()
        assert "/license" in reg.help_text()

    def test_status_without_activation(self, registry, keypair):
        reg, renderer = registry
        messages = self._run(reg, renderer, "status")
        assert any("free" in m for m in messages)

    def test_activate_then_status(self, registry, keypair):
        reg, renderer = registry
        private, _ = keypair
        _, key = issue("cli@example.com", private=private)
        messages = self._run(reg, renderer, "activate", key)
        assert any("PRO" in m for m in messages)
        renderer.messages.clear()
        messages = self._run(reg, renderer, "status")
        assert any("cli@example.com" in m for m in messages)

    def test_activate_rejects_garbage(self, registry):
        reg, renderer = registry
        messages = self._run(reg, renderer, "activate", "not-a-key")
        assert any("Activation failed" in m for m in messages)

    def test_deactivate(self, registry, keypair):
        reg, renderer = registry
        private, _ = keypair
        _, key = issue("cli@example.com", private=private)
        self._run(reg, renderer, "activate", key)
        renderer.messages.clear()
        messages = self._run(reg, renderer, "deactivate")
        assert any("deactivated" in m for m in messages)
        renderer.messages.clear()
        messages = self._run(reg, renderer, "status")
        assert any("free" in m for m in messages)

    def test_bad_usage_shows_usage(self, registry):
        reg, renderer = registry
        messages = self._run(reg, renderer, "activate")
        assert any("Usage" in m for m in messages)


# ═══════════════════════════════════════════════════════════════════════════════
#  Feature gates (agent --write, qlora, multi_model)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def pro_context(keypair, tmp_path, monkeypatch):
    """Patched store path + public key; returns an activator callable."""
    import licensing.store
    import licensing.verifier

    monkeypatch.setattr(licensing.store, "DEFAULT_LICENSE_PATH", tmp_path / "license.key")
    _, public_b64 = keypair
    monkeypatch.setattr(licensing.verifier, "PUBLIC_KEY_B64", public_b64)
    private, _ = keypair

    def _activate(email="pro@example.com"):
        _, key = issue(email, private=private)
        return activate(key)

    return _activate


class TestGate:
    def test_current_license_defaults_to_free(self, keypair, tmp_path, monkeypatch):
        import licensing.store

        monkeypatch.setattr(licensing.store, "DEFAULT_LICENSE_PATH", tmp_path / "license.key")
        lic = current_license()
        assert lic.plan == FREE
        assert not feature_enabled("agent_write")
        assert not feature_enabled("qlora")
        assert not feature_enabled("multi_model")

    def test_ensure_pro_raises_without_activation(self, keypair, tmp_path, monkeypatch):
        import licensing.store

        monkeypatch.setattr(licensing.store, "DEFAULT_LICENSE_PATH", tmp_path / "license.key")
        with pytest.raises(ProRequiredError, match="agent_write"):
            ensure_pro("agent_write")

    def test_pro_activation_enables_pro_features(self, pro_context):
        pro_context()
        assert feature_enabled("agent_write")
        assert feature_enabled("qlora")
        assert feature_enabled("multi_model")
        ensure_pro("qlora")

    def test_unknown_feature_not_gated(self, pro_context):
        test_unknown = feature_enabled("chat")
        assert test_unknown

    def test_activation_can_be_removed(self, pro_context):
        pro_context()
        clear_activation()
        assert not feature_enabled("agent_write")


class TestAgentWriteGate:
    @pytest.fixture()
    def registry(self, keypair, tmp_path, monkeypatch):
        import licensing.store
        import licensing.verifier

        monkeypatch.setattr(licensing.store, "DEFAULT_LICENSE_PATH", tmp_path / "license.key")
        _, public_b64 = keypair
        monkeypatch.setattr(licensing.verifier, "PUBLIC_KEY_B64", public_b64)

        class FakeRenderer:
            console = mock.MagicMock()

            def __init__(self):
                self.messages = []

            def info(self, text):
                self.messages.append(("info", text))

            def success(self, text):
                self.messages.append(("success", text))

            def error(self, text):
                self.messages.append(("error", text))

            def warn(self, text):
                self.messages.append(("warn", text))

        class FakeREPL:
            renderer = FakeRenderer()

        fake_settings = mock.MagicMock(rag=None, web=None)

        class FakeBackend:
            pass

        import cli.app as cli_app

        with mock.patch.multiple(
            cli_app,
            RAGConfig=mock.MagicMock(),
            RAGService=mock.MagicMock(),
            WebSearch=mock.MagicMock(),
            Assistant=mock.MagicMock(),
        ):
            registry = cli_app.build_registry(FakeREPL(), FakeBackend(), fake_settings)
        return registry, FakeREPL.renderer, keypair

    def _messages(self, renderer):
        return [text for _, text in renderer.messages]

    def test_write_blocked_for_free(self, registry):
        reg, renderer, _ = registry
        cmd = reg.get("agent")
        cmd.handler(["--write", "refactor the parser"])
        messages = self._messages(renderer)
        assert any("Pro feature" in m for m in messages)
        assert not any("WRITE MODE ENABLED" in m for m in messages)

    def test_readonly_agent_still_works_for_free(self, registry):
        from unittest import mock

        import cli.app as cli_app

        reg, renderer, _ = registry
        fake_agent = mock.MagicMock()
        fake_agent.run_iter.return_value = iter([])
        fake_agent.last_result.final_answer = None
        with mock.patch.object(cli_app, "CodingAgent", return_value=fake_agent):
            cmd = reg.get("agent")
            cmd.handler(["explain the parser"])
        assert not any("Pro feature" in m for m in self._messages(renderer))

    def test_write_allowed_for_pro(self, registry):
        from unittest import mock

        import cli.app as cli_app

        reg, renderer, keypair = registry
        private, _ = keypair
        _, key = issue("pro@example.com", private=private)
        activate(key)

        fake_agent = mock.MagicMock()
        fake_agent.run_iter.return_value = iter([])
        fake_agent.last_result.final_answer = None
        with mock.patch.object(cli_app, "CodingAgent", return_value=fake_agent):
            cmd = reg.get("agent")
            cmd.handler(["--write", "rename foo to bar"])
        assert not any("Pro feature" in m for m in self._messages(renderer))
        assert any("WRITE MODE ENABLED" in m for m in self._messages(renderer))


class TestStartupBackendGate:
    def _patch_env(self, monkeypatch, **env):
        for name in ("FLUXION_BACKEND", "FLUXION_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)

    def test_api_blocked_for_free(self, keypair, tmp_path, monkeypatch):
        import licensing.store

        monkeypatch.setattr(licensing.store, "DEFAULT_LICENSE_PATH", tmp_path / "license.key")
        self._patch_env(monkeypatch, FLUXION_API_KEY="sk-test")

        from cli.app import _startup_backend

        backend, gated = _startup_backend(mock.MagicMock())
        assert gated is True
        assert backend.__class__.__name__ == "OllamaBackend"

    def test_api_without_pro_falls_back_to_embedded_engine(self, keypair, tmp_path, monkeypatch):
        """A clean machine has no Ollama: fall back to the free llama.cpp engine."""
        import licensing.store

        monkeypatch.setattr(licensing.store, "DEFAULT_LICENSE_PATH", tmp_path / "license.key")
        self._patch_env(monkeypatch, FLUXION_API_KEY="sk-test")
        monkeypatch.setattr("core.backend_factory._llama_cpp_installed", lambda: True)

        from cli.app import _startup_backend
        from core.config import Settings

        backend, gated = _startup_backend(Settings())
        assert gated is True
        assert backend.__class__.__name__ == "LazyLlamaCppBackend"

    def test_llama_cpp_not_gated_for_free(self, keypair, tmp_path, monkeypatch):
        import licensing.store

        monkeypatch.setattr(licensing.store, "DEFAULT_LICENSE_PATH", tmp_path / "license.key")
        self._patch_env(monkeypatch, FLUXION_BACKEND="llama_cpp")

        from core.backend_factory import BackendFactory

        sentinel = object()
        monkeypatch.setattr(
            BackendFactory, "create_primary", staticmethod(lambda settings: sentinel)
        )

        from cli.app import _startup_backend

        backend, gated = _startup_backend(mock.MagicMock())
        assert gated is False
        assert backend is sentinel

    def test_ollama_never_gated(self, keypair, tmp_path, monkeypatch):
        import licensing.store

        monkeypatch.setattr(licensing.store, "DEFAULT_LICENSE_PATH", tmp_path / "license.key")
        self._patch_env(monkeypatch)

        from cli.app import _startup_backend

        _, gated = _startup_backend(mock.MagicMock())
        assert gated is False

    def test_api_allowed_for_pro(self, keypair, tmp_path, monkeypatch, pro_context):
        self._patch_env(monkeypatch, FLUXION_API_KEY="sk-test")
        pro_context()

        from core.backend_factory import BackendFactory

        sentinel = object()
        monkeypatch.setattr(
            BackendFactory, "create_primary", staticmethod(lambda settings: sentinel)
        )

        from cli.app import _startup_backend

        backend, gated = _startup_backend(mock.MagicMock())
        assert gated is False
        assert backend is sentinel


# ═══════════════════════════════════════════════════════════════════════════════
#  Issuer CLI: relative terms, device binding, journal
# ═══════════════════════════════════════════════════════════════════════════════


class TestIssuerCLI:
    def _run(self, tmp_path, keypair, *args):
        private_pem = tmp_path / "private.pem"
        private_pem.write_bytes(
            keypair[0].private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        registry = tmp_path / "registry.jsonl"
        code = issuer_main(
            [
                "--email", "cli@example.com",
                "--key-path", str(private_pem),
                "--registry", str(registry),
                *args,
            ]
        )
        assert code == 0
        return registry

    def test_months_days_and_journal(self, tmp_path, keypair, capsys):
        registry = self._run(tmp_path, keypair, "--months", "3")
        key = capsys.readouterr().out.strip()
        assert key and "." in key
        verified = verify_license(key, keypair[1])
        assert 89 <= (verified.expires_at - verified.issued_at).days <= 90
        entries = [json.loads(line) for line in registry.read_text().splitlines() if line]
        assert len(entries) == 1
        assert entries[0]["email"] == "cli@example.com"
        assert entries[0]["kid"] == verified.key_id

        self._run(tmp_path, keypair, "--days", "14")
        short = verify_license(capsys.readouterr().out.strip(), keypair[1])
        assert 13 <= (short.expires_at - short.issued_at).days <= 14

        self._run(tmp_path, keypair)
        perpetual = verify_license(capsys.readouterr().out.strip(), keypair[1])
        assert perpetual.expires_at is None
        kids = [e["kid"] for e in entries] + [short.key_id, perpetual.key_id]
        assert len(set(kids)) == len(kids)

    def test_device_flag_writes_bound_key(self, tmp_path, keypair, capsys, monkeypatch):
        from licensing import fingerprint

        self._run(tmp_path, keypair, "--device", "AB12-CD34-EF56-7890")
        key = capsys.readouterr().out.strip()
        monkeypatch.setattr(fingerprint, "device_code", lambda: "ab12cd34ef567890")
        verified = verify_license(key, keypair[1])
        assert verified.device == "AB12CD34EF567890"


class TestDeviceCode:
    def test_format(self):
        from licensing.fingerprint import device_code

        code = device_code()
        assert isinstance(code, str)
        assert len(code) == 19
        assert code.count("-") == 3
        assert code.replace("-", "").isalnum()

    def test_stable(self):
        from licensing.fingerprint import device_code

        assert device_code() == device_code()


class TestTrial:
    @pytest.fixture()
    def trial(self, tmp_path, monkeypatch):
        import licensing.trial as trial_module

        path = tmp_path / "state" / "trial.json"
        monkeypatch.setattr(trial_module, "trial_path", lambda: path)
        monkeypatch.setattr(trial_module, "device_code", lambda: "AAAA-BBBB-CCCC-DDDD")
        return trial_module

    def test_fresh_trial_has_three_edits(self, trial):
        assert trial.TRIAL_WRITES == 3
        assert trial.trial_remaining() == 3
        assert not trial.trial_path().exists()

    def test_consume_counts_down_and_stops_at_zero(self, trial):
        assert [trial.trial_consume() for _ in range(4)] == [True, True, True, False]
        assert trial.trial_remaining() == 0

    def test_first_consume_binds_the_device(self, trial):
        assert trial.trial_consume()
        state = json.loads(trial.trial_path().read_text(encoding="utf-8"))
        assert state == {"device": "AAAA-BBBB-CCCC-DDDD", "remaining": 2}

    def test_state_from_another_device_is_spent(self, trial, monkeypatch):
        assert trial.trial_consume()
        monkeypatch.setattr(trial, "device_code", lambda: "EEEE-FFFF-0000-1111")
        assert trial.trial_remaining() == 0
        assert not trial.trial_consume()

    def test_reset_restores_the_trial(self, trial):
        for _ in range(3):
            trial.trial_consume()
        trial.trial_reset()
        assert trial.trial_remaining() == 3
        trial.trial_reset()

    def test_broken_or_inflated_state(self, trial):
        path = trial.trial_path()
        path.parent.mkdir(parents=True)
        path.write_text("not json", encoding="utf-8")
        assert trial.trial_remaining() == 0
        path.write_text('{"device": "AAAA-BBBB-CCCC-DDDD", "remaining": 99}', encoding="utf-8")
        assert trial.trial_remaining() == 3

    def test_frozen_build_keeps_state_in_appdata(self, tmp_path, monkeypatch):
        import importlib.util
        import sys

        import licensing.trial as trial_module

        spec = importlib.util.spec_from_file_location(
            "licensing.trial_unpatched", trial_module.__file__
        )
        fresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert fresh.trial_path() == tmp_path / "Fluxion" / "trial.json"
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        assert fresh.trial_path().parts[-2:] == ("data", "trial.json")


class TestUpdatePeriod:
    @staticmethod
    def _issued(year: int, month: int, day: int) -> License:
        return License(
            email="buyer@example.com", plan=PRO,
            issued_at=datetime(year, month, day, tzinfo=timezone.utc),
        )

    @pytest.fixture()
    def build(self, monkeypatch):
        import licensing.release as release

        def set_date(value: str) -> None:
            monkeypatch.setattr(release, "RELEASE_DATE", value)

        return set_date

    def test_build_inside_the_year_is_covered(self, build):
        build("2027-03-01")
        lic = self._issued(2026, 10, 7)
        assert lic.updates_until == datetime(2027, 10, 7, tzinfo=timezone.utc)
        assert not lic.updates_lapsed
        assert lic.is_pro
        assert lic.feature_enabled("agent_write")

    def test_build_released_after_the_year_needs_renewal(self, build):
        build("2027-10-08")
        lic = self._issued(2026, 10, 7)
        assert lic.updates_lapsed
        assert not lic.is_pro
        assert not lic.feature_enabled("agent_write")
        assert lic.feature_enabled("chat")

    def test_last_covered_day_still_works(self, build):
        build("2027-10-07")
        assert self._issued(2026, 10, 7).is_pro

    def test_old_build_keeps_working_forever(self, build):
        build("2026-10-06")
        assert self._issued(2020, 1, 1).updates_lapsed
        assert self._issued(2026, 10, 7).is_pro

    def test_renewal_is_a_fresh_key(self, build):
        build("2028-01-15")
        assert not self._issued(2026, 10, 7).is_pro
        assert self._issued(2027, 10, 20).is_pro

    def test_key_without_issue_date_is_not_limited(self, build):
        build("2099-01-01")
        lic = License(email="old@example.com", plan=PRO)
        assert lic.updates_until is None
        assert lic.is_pro

    def test_release_date_is_a_real_date(self):
        from licensing import RELEASE_DATE, UPDATE_PERIOD_DAYS, release_date

        assert release_date().strftime("%Y-%m-%d") == RELEASE_DATE
        assert UPDATE_PERIOD_DAYS == 365

