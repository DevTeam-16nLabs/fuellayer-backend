import json

import httpx
import pytest

from fuellayer.core.config import Settings
from fuellayer.integrations import ai
from fuellayer.modules.courses import receipts
from fuellayer.modules.courses.schemas import ExtractedReceipt


@pytest.mark.parametrize(
    ("router_key", "direct_key", "model", "provider", "model_id"),
    [
        ("router-test", "direct-test", None, "OpenRouter", "openai/gpt-4.1-mini"),
        (None, "direct-test", None, "OpenAI", "gpt-4.1-mini"),
        (None, None, None, None, "gpt-4.1-mini"),
        ("router-test", None, "google/gemini-2.5-flash", "OpenRouter", "google/gemini-2.5-flash"),
        (None, "direct-test", "openai/gpt-4.1-mini", "OpenAI", "gpt-4.1-mini"),
    ],
)
def test_receipt_provider_selection(router_key, direct_key, model, provider, model_id):
    config = Settings(
        _env_file=None,
        openrouter_api_key=router_key,
        openai_api_key=direct_key,
        receipt_model=model,
    )
    assert config.receipt_provider == provider
    assert config.receipt_api_key == (router_key or direct_key)
    assert config.receipt_model_id == model_id


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["OpenRouter", "OpenAI"])
@pytest.mark.parametrize("completed", [True, False])
async def test_receipt_wire_contract_and_incomplete_response(monkeypatch, provider, completed):
    router = provider == "OpenRouter"
    config = Settings(
        _env_file=None,
        openrouter_api_key="router-test" if router else None,
        openai_api_key="direct-test",
        receipt_model=None,
    )
    monkeypatch.setattr(receipts, "settings", config)
    result = ExtractedReceipt(
        readable=False, merchant=None, purchase_date=None, receipt_number=None, total=None, lines=[]
    )
    image = "data:image/png;base64,c3ludGhldGlj"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == config.receipt_endpoint
        assert request.headers["authorization"] == f"Bearer {config.receipt_api_key}"
        body = json.loads(request.content)
        assert body["model"] == config.receipt_model_id
        assert body["store"] is False
        assert body["text"]["format"]["strict"] is True
        assert body["input"][1]["content"][0]["image_url"] == image
        if router:
            assert body["provider"]["require_parameters"] is True
        else:
            assert "provider" not in body
        return httpx.Response(
            200,
            json={
                "status": "completed" if completed else "incomplete",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": result.model_dump_json()}],
                    }
                ],
            },
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        ai.httpx,
        "AsyncClient",
        lambda **kw: original_client(**kw, transport=httpx.MockTransport(handler)),
    )
    if completed:
        assert await receipts.extract(image) == result
    else:
        with pytest.raises(ValueError, match="incomplete_analysis"):
            await receipts.extract(image)
