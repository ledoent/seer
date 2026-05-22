import hashlib
import hmac
import json
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Blueprint, Flask
from johen import change_watcher
from pydantic import BaseModel

from seer.configuration import AppConfig
from seer.dependency_injection import Module, resolve
from seer.json_api import PublicKeyBytes, json_api


class DummyRequest(BaseModel):
    thing: str
    b: int


class DummyResponse(BaseModel):
    blah: str


def test_json_api_decorator():
    app = Flask(__name__)
    blueprint = Blueprint("blueprint", __name__)
    test_client = app.test_client()

    @json_api(blueprint, "/v0/some/url")
    def my_endpoint(request: DummyRequest) -> DummyResponse:
        assert request.thing == "thing"
        assert request.b == 12
        return DummyResponse(blah="do it")

    app.register_blueprint(blueprint)

    app_config = resolve(AppConfig)
    app_config.IGNORE_API_AUTH = True

    response = test_client.post("/v0/some/url", json={"thing": "thing", "b": 12})
    assert response.status_code == 200
    assert response.get_json() == {"blah": "do it"}

    assert my_endpoint(DummyRequest(thing="thing", b=12)) == DummyResponse(blah="do it")


def test_json_api_bearer_token_auth():
    app = Flask(__name__)
    blueprint = Blueprint("blueprint", __name__)
    test_client = app.test_client()

    @json_api(blueprint, "/v0/some/url")
    def my_endpoint(request: DummyRequest) -> DummyResponse:
        return DummyResponse(blah="do it")

    app.register_blueprint(blueprint)

    app_config = resolve(AppConfig)
    app_config.IGNORE_API_AUTH = False
    app_config.DEV = False

    pk = resolve(PublicKeyBytes)
    pk.bytes = b"mock_public_key"

    with patch("seer.json_api.jwt.decode") as mock_jwt_decode:
        # Test valid token
        headers = {"Authorization": "Bearer valid_token"}
        response = test_client.post(
            "/v0/some/url", json={"thing": "thing", "b": 12}, headers=headers
        )
        assert response.status_code == 200
        mock_jwt_decode.assert_called_once_with(
            "valid_token", b"mock_public_key", algorithms=["RS256"]
        )

        # Test invalid token
        mock_jwt_decode.side_effect = jwt.InvalidTokenError
        response = test_client.post(
            "/v0/some/url", json={"thing": "thing", "b": 12}, headers=headers
        )
        assert response.status_code == 401
        assert b"Invalid token" in response.data


def test_json_api_auth_not_enforced():
    app = Flask(__name__)
    blueprint = Blueprint("blueprint", __name__)
    test_client = app.test_client()

    @json_api(blueprint, "/v0/some/url")
    def my_endpoint(request: DummyRequest) -> DummyResponse:
        return DummyResponse(blah="do it")

    app.register_blueprint(blueprint)

    app_config = resolve(AppConfig)
    app_config.IGNORE_API_AUTH = False

    # Test that request is allowed without any auth when ENFORCE_API_AUTH is False
    response = test_client.post("/v0/some/url", json={"thing": "thing", "b": 12})
    assert response.status_code == 200
    assert response.get_json() == {"blah": "do it"}


def test_json_api_auth_with_real_jwt():

    app_config = resolve(AppConfig)
    app_config.IGNORE_API_AUTH = False
    app_config.DEV = False

    # Generate a test RSA key pair
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    # Convert public key to PEM format
    public_key_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
    )

    # Create a test JWT token
    payload = {"sub": "1234567890", "name": "Test User", "iat": 1516239022}
    token = jwt.encode(payload, private_key, algorithm="RS256")

    module = Module()
    module.constant(PublicKeyBytes, PublicKeyBytes(bytes=public_key_pem))
    with module:
        app = Flask(__name__)
        blueprint = Blueprint("blueprint", __name__)
        test_client = app.test_client()

        @json_api(blueprint, "/v0/some/url")
        def my_endpoint(request: DummyRequest) -> DummyResponse:
            return DummyResponse(blah="do it")

        app.register_blueprint(blueprint)

        # Test valid token
        headers = {"Authorization": f"Bearer {token}"}
        response = test_client.post(
            "/v0/some/url", json={"thing": "thing", "b": 12}, headers=headers
        )
        assert response.status_code == 200
        assert response.get_json() == {"blah": "do it"}

        # Test invalid token
        invalid_token = jwt.encode(payload, "wrong_key", algorithm="HS256")
        headers = {"Authorization": f"Bearer {invalid_token}"}
        response = test_client.post(
            "/v0/some/url", json={"thing": "thing", "b": 12}, headers=headers
        )
        assert response.status_code == 401
        assert b"Invalid token" in response.data

        # Test expired token
        import time

        expired_payload = {"exp": int(time.time()) - 300}  # Token expired 5 minutes ago
        expired_token = jwt.encode(expired_payload, private_key, algorithm="RS256")
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = test_client.post(
            "/v0/some/url", json={"thing": "thing", "b": 12}, headers=headers
        )
        assert response.status_code == 401
        assert b"Token has expired" in response.data


@pytest.mark.skip(reason="Enable auth")
def test_json_api_signature_strict_mode_ignores_rpcsignature():
    app = Flask(__name__)
    blueprint = Blueprint("blueprint", __name__)
    test_client = app.test_client()

    @json_api(blueprint, "/v0/some/url")
    def my_endpoint(request: DummyRequest) -> DummyResponse:
        return DummyResponse(blah="do it")

    app.register_blueprint(blueprint)

    headers = {}
    payload = {"thing": "thing", "b": 12}
    path = "/v0/some/url"
    status_code_watcher = change_watcher(
        lambda: test_client.post(path, json=payload, headers=headers).status_code
    )

    with Module() as injector:
        injector.get(AppConfig).JSON_API_SHARED_SECRETS = ["secret-one", "secret-two"]

        with status_code_watcher as changed:
            headers["Authorization"] = "Rpcsignature rpc0:some-token"

        assert changed.result == 200


def test_json_api_signature_strict_mode():
    app = Flask(__name__)
    blueprint = Blueprint("blueprint", __name__)
    test_client = app.test_client()

    @json_api(blueprint, "/v0/some/url")
    def my_endpoint(request: DummyRequest) -> DummyResponse:
        return DummyResponse(blah="do it")

    app.register_blueprint(blueprint)

    headers = {}
    payload = {"thing": "thing", "b": 12}
    path = "/v0/some/url"
    status_code_watcher = change_watcher(
        lambda: test_client.post(path, json=payload, headers=headers).status_code
    )

    with Module() as injector:
        injector.get(AppConfig).JSON_API_SHARED_SECRETS = ["secret-one", "secret-two"]

        with status_code_watcher as changed:
            headers["Authorization"] = "Rpcsignature rpc0:some-token"

        assert changed.from_value(200)
        assert changed.to_value(401)

        with status_code_watcher as changed:
            signature = hmac.new(
                "secret-one".encode(),
                json.dumps(payload, sort_keys=True).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            headers["Authorization"] = f"Rpcsignature rpc0:{signature}"
        assert changed.to_value(200)


class TestSignatureFailureDiagnostics:
    """Pin the diagnostic context that lands on a failed-signature event.

    Background: ledoent/seer Sentry issue #76 fired 118 events / 14d as
    "No signature matches found" with zero context — culprit URL, source
    IP, and body shape were all invisible. Diagnosis took live SSH +
    clickhouse queries to identify the caller (compose-internal
    post-process-forwarder auto-summary). Pinning the enriched context +
    fingerprint here so future investigators can read it off the event.
    """

    def _make_app(self):
        app = Flask(__name__)
        blueprint = Blueprint("blueprint", __name__)

        @json_api(blueprint, "/v0/some/url")
        def my_endpoint(request: DummyRequest) -> DummyResponse:
            return DummyResponse(blah="ok")

        app.register_blueprint(blueprint)
        return app

    @patch("seer.json_api.sentry_sdk")
    def test_bad_prefix_capture_includes_request_context(self, mock_sdk):
        app = self._make_app()
        test_client = app.test_client()

        with Module() as injector:
            injector.get(AppConfig).JSON_API_SHARED_SECRETS = ["secret-one"]
            test_client.post(
                "/v0/some/url",
                json={"thing": "x", "b": 1},
                headers={"Authorization": "Rpcsignature notrpc0:xyz"},
            )

        # capture_message called with the bad-prefix branch
        call = next(
            c
            for c in mock_sdk.capture_message.call_args_list
            if "did not start with rpc0:" in c.args[0]
        )
        ctx = call.kwargs["contexts"]["request"]
        assert ctx["url"].endswith("/v0/some/url")
        assert ctx["method"] == "POST"
        # body_len matches what flask received
        assert ctx["body_len"] > 0
        # signature_prefix truncated, no full secret leakage
        assert "..." in ctx["signature_prefix"] or len(ctx["signature_prefix"]) <= 16

    @patch("seer.json_api.sentry_sdk")
    def test_no_match_capture_enriches_via_scope_and_fingerprint(self, mock_sdk):
        app = self._make_app()
        test_client = app.test_client()

        # Build a request with VALID rpc0: prefix but wrong signature hex
        with Module() as injector:
            injector.get(AppConfig).JSON_API_SHARED_SECRETS = ["secret-one"]
            test_client.post(
                "/v0/some/url",
                json={"thing": "x", "b": 1},
                headers={"Authorization": "Rpcsignature rpc0:deadbeef"},
            )

        # push_scope was used so subsequent capture_message picks up the
        # scope's context + fingerprint
        assert mock_sdk.push_scope.called
        scope = mock_sdk.push_scope.return_value.__enter__.return_value

        # The set_context call carried the enriched request dict
        ctx_call = next(c for c in scope.set_context.call_args_list if c.args[0] == "request")
        ctx = ctx_call.args[1]
        assert ctx["url"].endswith("/v0/some/url")
        assert "body_prefix" in ctx and ctx["body_prefix"]  # non-empty
        assert ctx["signature_prefix"].startswith("rpc0:deadbeef"[:16])

        # The fingerprint groups all such failures into one Sentry issue
        # regardless of body shape — so a storm stays a single fire.
        assert scope.fingerprint == ["json_api.signature_mismatch"]

        # And the capture_message itself is at warning level
        capture_calls = [
            c
            for c in mock_sdk.capture_message.call_args_list
            if "No signature matches found" in c.args[0]
        ]
        assert capture_calls
        assert capture_calls[0].kwargs.get("level") == "warning"

    @patch("seer.json_api.sentry_sdk")
    def test_signature_failure_context_does_not_leak_full_signature(self, mock_sdk):
        """Context dict caps the signature at 16 chars to avoid leaking
        anything sensitive even though the signature itself is a public-ish
        hex digest.
        """
        full_sig_hex = "deadbeef" * 8  # 64 chars
        app = self._make_app()
        test_client = app.test_client()
        with Module() as injector:
            injector.get(AppConfig).JSON_API_SHARED_SECRETS = ["secret-one"]
            test_client.post(
                "/v0/some/url",
                json={"thing": "x", "b": 1},
                headers={"Authorization": f"Rpcsignature rpc0:{full_sig_hex}"},
            )

        scope = mock_sdk.push_scope.return_value.__enter__.return_value
        ctx_call = next(c for c in scope.set_context.call_args_list if c.args[0] == "request")
        ctx = ctx_call.args[1]
        # Truncated representation only
        assert full_sig_hex not in ctx["signature_prefix"]
        assert len(ctx["signature_prefix"]) <= 20
