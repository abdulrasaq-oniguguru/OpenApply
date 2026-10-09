from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    conversation_id: str
    channel: str
    role: str
    text: str
    linked_type: str | None = None
    linked_id: str | None = None
    created_at: str


class ConversationReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    user_message: Message
    assistant_message: Message
