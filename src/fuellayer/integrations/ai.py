"""Small schema-only Responses transport shared by independent AI features."""

import asyncio
from typing import Any

import httpx


async def structured_response(
    *,
    endpoint: str,
    api_key: str,
    model: str,
    prompt: str,
    content: list[dict[str, Any]],
    schema: dict[str, Any],
    name: str,
    openrouter: bool = False,
    timeout: float = 90,
    max_output_tokens: int = 10000,
) -> str:
    async with asyncio.timeout(timeout), httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                **({"provider": {"require_parameters": True}} if openrouter else {}),
                "store": False,
                "input": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": content},
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": name,
                        "strict": True,
                        "schema": schema,
                    }
                },
                "max_output_tokens": max_output_tokens,
            },
        )
        response.raise_for_status()
        body = response.json()
    if body.get("status") != "completed":
        raise ValueError("incomplete_analysis")
    return "".join(
        block["text"]
        for output in body.get("output", [])
        if output.get("type") == "message"
        for block in output.get("content", [])
        if block.get("type") == "output_text"
    )
