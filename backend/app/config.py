import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://baton:baton@localhost:4569/baton"
)
DATA_SOURCES_DIR = os.environ.get("DATA_SOURCES_DIR", "/data/sources")

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
LLM_MODEL = os.environ.get("BATON_LLM_MODEL", "us.anthropic.claude-sonnet-4-6")
EMBED_MODEL = os.environ.get("BATON_EMBED_MODEL", "cohere.embed-multilingual-v3")
EMBED_DIM = 1024

# 유사도 판단 임계값
SIM_WIKI_MATCH = 0.60   # 새 클레임 ↔ 기존 클레임: 이 이상이면 같은 주제 위키로 판단
SIM_ANSWER_MIN = 0.55   # 챗봇: 이 미만이면 근거 0건 → 역질문 제안 (cohere v3 한국어 무관 쌍 baseline ~0.5)
SIM_LINT_PAIR = 0.70    # 린트: 모순 후보 쌍 탐색 임계값
