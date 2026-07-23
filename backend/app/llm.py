"""LLM + 임베딩 래퍼. LLM 호출은 전부 이 모듈을 거친다.

LLM 프로바이더는 BATON_LLM_PROVIDER 환경변수로 전환한다:
- "bedrock"  : Claude (AnthropicBedrock)
- "deepseek" : DeepSeek 직접 API (OpenAI 호환 /chat/completions, SSE 스트리밍)
임베딩은 프로바이더와 무관하게 Bedrock cohere를 쓴다 (deepseek는 임베딩 API가 없다).
"""
import json
import logging
import re
import time

import boto3
import httpx
from anthropic import AnthropicBedrock

from .config import (
    AWS_REGION,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MAX_TOKENS,
    DEEPSEEK_MODEL,
    EMBED_MODEL,
    LLM_MODEL,
    LLM_PROVIDER,
)

log = logging.getLogger("baton.llm")

_client = AnthropicBedrock(aws_region=AWS_REGION)
_bedrock_rt = boto3.client("bedrock-runtime", region_name=AWS_REGION)


def _require_deepseek_key():
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "BATON_LLM_PROVIDER=deepseek 인데 DEEPSEEK_API_KEY 가 없습니다. .env 에 키를 추가하세요."
        )


def _ds_headers() -> dict:
    return {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}


def _ds_body(system: str, user: str, max_tokens: int, stream: bool = False) -> dict:
    return {
        "model": DEEPSEEK_MODEL,
        "max_tokens": min(max_tokens, DEEPSEEK_MAX_TOKENS),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": stream,
    }


def _complete_once(system: str, user: str, max_tokens: int) -> str:
    if LLM_PROVIDER == "deepseek":
        _require_deepseek_key()
        resp = httpx.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers=_ds_headers(),
            json=_ds_body(system, user, max_tokens),
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    resp = _client.messages.create(
        model=LLM_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def complete(system: str, user: str, max_tokens: int = 4096) -> str:
    """단일 텍스트 완성. 오류 시 1회 재시도."""
    for attempt in (1, 2):
        try:
            return _complete_once(system, user, max_tokens)
        except Exception as e:  # noqa: BLE001
            log.warning("LLM 호출 실패(%d/2, %s): %s", attempt, LLM_PROVIDER, e)
            if attempt == 2:
                raise
            time.sleep(2)
    raise RuntimeError("unreachable")


def stream(system: str, user: str, max_tokens: int = 4096):
    """텍스트 스트리밍 제너레이터 (챗봇용)."""
    if LLM_PROVIDER == "deepseek":
        _require_deepseek_key()
        with httpx.stream(
            "POST",
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers=_ds_headers(),
            json=_ds_body(system, user, max_tokens, stream=True),
            timeout=300,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                delta = json.loads(data)["choices"][0].get("delta", {})
                if delta.get("content"):
                    yield delta["content"]
        return
    with _client.messages.stream(
        model=LLM_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as s:
        for text in s.text_stream:
            yield text


def extract_json(system: str, user: str, max_tokens: int = 16000):
    """JSON 강제 출력. 파싱 실패 시 1회 재시도."""
    sys_json = system + "\n\n반드시 유효한 JSON만 출력한다. 마크다운 코드펜스·설명 금지."
    for attempt in (1, 2):
        raw = complete(sys_json, user, max_tokens=max_tokens)
        try:
            return _parse_json(raw)
        except ValueError as e:
            log.warning("JSON 파싱 실패(%d/2): %s", attempt, e)
            if attempt == 2:
                raise
            user = user + "\n\n(직전 출력이 유효한 JSON이 아니었다. JSON만 다시 출력할 것)"
    raise RuntimeError("unreachable")


def _parse_json(raw: str):
    raw = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if m:
        raw = m.group(1).strip()
    start = min([i for i in (raw.find("{"), raw.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("JSON 시작 문자를 찾지 못함")
    return json.loads(raw[start:])


def embed(texts: list[str], input_type: str = "search_document") -> list[list[float]]:
    """cohere.embed-multilingual-v3 (1024d). input_type: search_document | search_query"""
    out: list[list[float]] = []
    for i in range(0, len(texts), 96):  # cohere 배치 한도
        body = json.dumps(
            {"texts": [t[:2000] for t in texts[i : i + 96]], "input_type": input_type, "truncate": "END"}
        )
        resp = _bedrock_rt.invoke_model(modelId=EMBED_MODEL, body=body)
        out.extend(json.loads(resp["body"].read())["embeddings"])
    return out
