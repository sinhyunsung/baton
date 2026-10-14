# Baton — 사내 지식 LLM 위키

사내 업무 데이터(슬랙·문서·이슈 등 어떤 소스든)를 **클레임(업무 명제)** 으로 구조화해 살아있는 위키를 자동 구축·검증하고, 출처 추적 챗봇을 제공하는 플랫폼.

## 실행

```bash
cp .env.example .env   # DEEPSEEK_API_KEY · AWS 자격증명(Bedrock cohere 임베딩 전용, us-east-1)
docker compose up -d --build
```

| 컨테이너 | 포트 | 역할 |
|---|---|---|
| `baton_frontend` | **4567** | Next.js UI — http://localhost:4567 |
| `baton_backend` | 4568 | FastAPI — 파이프라인·LLM·검색 전부 여기 |
| `baton_db` | 4569 | PostgreSQL 16 + pgvector |

## 파이프라인

수집(sha256 중복 제거·버전) → 전처리+클레임 추출(DeepSeek, 문서당 1회) → 임베딩(cohere multilingual v3)
→ **위키 갱신안**(사람이 승인해야 반영) → 배치 린트(모순·빈틈 → 확인 요청함 역질문) → 검색·챗봇(클레임 유사도 역추적)

## CLI

```bash
docker exec baton_backend python -m app.cli ingest --limit 10   # 소량 인제스트(비용 가드)
docker exec baton_backend python -m app.cli ingest --auto-approve  # 대량 인제스트(갱신안 자동 승인)
docker exec baton_backend python -m app.cli lint                # 배치 린트
docker exec baton_backend python -m app.cli retry               # 실패 문서 재시도
```

## 설계

확정된 설계·규칙은 `/home/shs/batone/.claude/skills/baton-dev/SKILL.md` 참조 (개발 시 반드시 로드).
비용 참고: 문서당 DeepSeek 약 2회 호출 (전처리+클레임 1회, 위키 갱신안 1회). Bedrock 은 cohere 임베딩에만 쓴다 — Claude 경로는 지웠다 (2026-10-14).
