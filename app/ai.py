from fastapi import APIRouter, Request

from app.dependencies import UserDep, require_editor
from app.schemas import APIModel, Body

router = APIRouter(prefix="/api/v1/ai", tags=["gigachat"])


class ChatInput(APIModel):
    prompt: Body


class ChatOutput(APIModel):
    text: str
    model: str


@router.post("/chat", response_model=ChatOutput)
async def chat(body: ChatInput, request: Request, user: UserDep):
    require_editor(user)
    text = await request.app.state.gigachat.ask(body.prompt)
    return ChatOutput(text=text, model=request.app.state.settings.gigachat_model)
