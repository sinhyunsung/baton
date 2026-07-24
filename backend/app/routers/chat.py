from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ..db import get_session
from ..pipeline.chat import answer_stream, create_backquestion, retrieve

router = APIRouter(prefix="/api", tags=["chat"])


class ChatIn(BaseModel):
    question: str
    history: list[dict] = []   # [{"role": "user"|"assistant", "text": "..."}] 최근 순서대로


@router.post("/chat")
def chat(body: ChatIn, db: Session = Depends(get_session)):
    return EventSourceResponse(answer_stream(db, body.question, history=body.history))


@router.post("/chat/backquestion")
def chat_backquestion(body: ChatIn, db: Session = Depends(get_session)):
    issue = create_backquestion(db, body.question)
    return {"issue_id": issue.id}


@router.get("/search")
def search(q: str, db: Session = Depends(get_session)):
    """위키 검색 화면용: 역추적 결과(요약 답변은 프론트에서 /chat SSE로)."""
    r = retrieve(db, q)
    return {
        "search_text": r["search_text"],
        "matches": [{
            "claim_id": m["claim"].id, "claim_text": m["claim"].rendered_text,
            "sim": m["sim"], "human_confirmed": m["claim"].human_confirmed,
            "claim_type": m["claim"].claim_type,
            "source": {"id": m["source"].id, "type": m["source"].source_type,
                       "title": m["source"].title},
            "pages": [{"id": p.id, "title": p.title, "folder_path": p.folder_path}
                      for p in m["pages"]],
        } for m in r["matches"]],
    }
