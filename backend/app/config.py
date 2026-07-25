import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://baton:baton@localhost:4569/baton"
)
DATA_SOURCES_DIR = os.environ.get("DATA_SOURCES_DIR", "/data/sources")

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# LLM 프로바이더: "bedrock" (Claude) | "deepseek" (직접 API, 저비용)
LLM_PROVIDER = os.environ.get("BATON_LLM_PROVIDER", "bedrock").lower()
LLM_MODEL = os.environ.get("BATON_LLM_MODEL", "us.anthropic.claude-sonnet-4-6")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("BATON_DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MAX_TOKENS = int(os.environ.get("BATON_DEEPSEEK_MAX_TOKENS", "8000"))  # deepseek 출력 한도
DEEPSEEK_TEMPERATURE = float(os.environ.get("BATON_DEEPSEEK_TEMPERATURE", "0.2"))  # JSON 안정성 위해 낮게

# 임베딩은 프로바이더와 무관하게 Bedrock cohere 사용 (deepseek는 임베딩 API 없음, 비용도 미미)
EMBED_MODEL = os.environ.get("BATON_EMBED_MODEL", "cohere.embed-multilingual-v3")
EMBED_DIM = 1024

# 병렬 처리: 인제스트(문서 단위)·린트(페이지 단위) 동시 실행 수
PARALLEL_WORKERS = int(os.environ.get("BATON_PARALLEL", "4"))

# 클레임 누락 스윕(2차 패스): 본문을 한 번 더 훑어 안 뽑힌 사실을 추가 추출
CLAIM_SWEEP = os.environ.get("BATON_CLAIM_SWEEP", "1") == "1"

# 유사도 판단 임계값
SIM_WIKI_MATCH = 0.60   # 새 클레임 ↔ 기존 클레임: 이 이상이면 같은 주제 위키로 판단
SIM_ANSWER_MIN = 0.55   # 챗봇: 이 미만이면 근거 0건 → 역질문 제안 (cohere v3 한국어 무관 쌍 baseline ~0.5)
SIM_LINT_PAIR = 0.70    # (구) 문장 전체 유사 임계 — 요소 매칭 도입 후 보조용
SIM_ELEMENT = 0.70      # 린트: 요소(속성/값)별 "같다" 판정 임계
SIM_SUBJECT_OWNER = 0.85  # 담당자 충돌은 주어가 이 이상 같을 때만 (서식 유사 오탐 방지)
