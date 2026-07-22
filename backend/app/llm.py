"""LLM(Bedrock Sonnet 4.6) + 임베딩(cohere multilingual) 래퍼. LLM 호출은 전부 이 모듈을 거친다."""
import json
import logging
import re
import time

import boto3
from anthropic import AnthropicBedrock

from .config import AWS_REGION, EMBED_MODEL, LLM_MODEL

log = logging.getLogger("baton.llm")

_client = AnthropicBedrock(aws_region=AWS_REGION)
_bedrock_rt = boto3.client("bedrock-runtime", region_name=AWS_REGION)


def complete(system: str, user: str, max_tokens: int = 4096) -> str:
    """단일 텍스트 완성. 오류 시 1회 재시도."""
    for attempt in (1, 2):
        try:
            resp = _client.messages.create(
                model=LLM_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return "".join(b.text for b in resp.content if b.type == "text")
        except Exception as e:  # noqa: BLE001
            log.warning("LLM 호출 실패(%d/2): %s", attempt, e)
            if attempt == 2:
                raise
            time.sleep(2)
    raise RuntimeError("unreachable")


def stream(system: str, user: str, max_tokens: int = 4096):
    """텍스트 스트리밍 제너레이터 (챗봇용)."""
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
