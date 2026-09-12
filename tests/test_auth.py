from collections.abc import Callable
from time import time
from typing import Annotated

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from fuellayer.core import auth
from fuellayer.core.config import Settings

ISSUER = "https://clerk.fuellayer.16nlabs.com"
WEB_ORIGIN = "https://fuellayer.16nlabs.com"


@pytest.fixture
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def token(signing_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    def sign(*, omit: tuple[str, ...] = (), **overrides: object) -> str:
        now = int(time())
        claims: dict[str, object] = {
            "iss": ISSUER,
            "sub": "user_test_native",
            "sid": "sess_test_native",
            "iat": now,
            "nbf": now - 10,
            "exp": now + 60,
            "v": 2,
        }
        claims.update(overrides)
        for name in omit:
            claims.pop(name, None)
        return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "test-key"})

    return sign


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, signing_key: rsa.RSAPrivateKey) -> TestClient:
    public_key = (
        signing_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    monkeypatch.setattr(
        auth,
        "settings",
        Settings(
            _env_file=None,
            environment="production",
            clerk_secret_key="sk_test_local_fixture",
            clerk_jwt_key=public_key,
            clerk_jwt_issuer=ISSUER,
            clerk_authorized_parties=f"fuellayer://,{WEB_ORIGIN}",
        ),
    )
    app = FastAPI()

    @app.get("/private")
    async def private(
        subject: Annotated[auth.AuthSubject, Depends(auth.require_auth_subject)],
    ) -> dict[str, str]:
        return {"subject": subject.subject}

    return TestClient(app)


def test_native_bearer_without_web_origin_is_accepted(
    client: TestClient, token: Callable[..., str]
) -> None:
    response = client.get("/private", headers={"Authorization": f"Bearer {token()}"})
    assert response.status_code == 200
    assert response.json() == {"subject": "user_test_native"}


@pytest.mark.parametrize("transport", ["bearer", "cookie"])
def test_allowed_web_session_is_accepted(
    client: TestClient, token: Callable[..., str], transport: str
) -> None:
    value = token(azp=WEB_ORIGIN)
    headers = {"Origin": WEB_ORIGIN}
    headers.update(
        {"Authorization": f"Bearer {value}"}
        if transport == "bearer"
        else {"Cookie": f"__session={value}"}
    )
    assert client.get("/private", headers=headers).status_code == 200


@pytest.mark.parametrize(
    "claims",
    [
        {"azp": "https://attacker.invalid"},
        {"azp": ""},
        {"azp": None},
        {"azp": [WEB_ORIGIN]},
        {"iss": "https://another-instance.clerk.accounts.dev"},
        {"exp": 1},
        {"nbf": 4102444800},
        {"iat": 4102444800},
        {"sub": ""},
        {"sid": ""},
        {"sts": "pending"},
    ],
)
def test_invalid_signed_session_is_rejected(
    client: TestClient, token: Callable[..., str], claims: dict[str, object]
) -> None:
    response = client.get("/private", headers={"Authorization": f"Bearer {token(**claims)}"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_session"


@pytest.mark.parametrize("missing", ["iss", "sub", "sid", "exp", "nbf", "iat"])
def test_required_session_claims_cannot_be_omitted(
    client: TestClient, token: Callable[..., str], missing: str
) -> None:
    value = token(omit=(missing,))
    assert client.get("/private", headers={"Authorization": f"Bearer {value}"}).status_code == 401


def test_cookie_cannot_use_native_origin_exception(
    client: TestClient, token: Callable[..., str]
) -> None:
    assert client.get("/private", headers={"Cookie": f"__session={token()}"}).status_code == 401


def test_request_with_origin_cannot_use_native_exception(
    client: TestClient, token: Callable[..., str]
) -> None:
    response = client.get(
        "/private", headers={"Authorization": f"Bearer {token()}", "Origin": WEB_ORIGIN}
    )
    assert response.status_code == 401


def test_forged_signature_is_rejected(client: TestClient) -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {"sub": "user_test_native", "exp": int(time()) + 60}, other_key, algorithm="RS256"
    )
    assert client.get("/private", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_missing_token_is_rejected(client: TestClient) -> None:
    assert client.get("/private").status_code == 401


def test_rejection_logs_reason_without_credentials(
    client: TestClient, token: Callable[..., str], caplog: pytest.LogCaptureFixture
) -> None:
    value = token(azp="https://attacker.invalid")
    client.get("/private", headers={"Authorization": f"Bearer {value}"})
    assert "token-invalid-authorized-parties" in caplog.text
    assert value not in caplog.text
    assert "user_test_native" not in caplog.text


def test_production_requires_explicit_issuer(client: TestClient) -> None:
    auth.settings.clerk_jwt_issuer = None
    assert client.get("/private").status_code == 503
