"""Chat with Transcript module (F2).

Provides a ChatSession class that allows users to ask questions
about a meeting transcript using Claude as the AI backend.
"""

import asyncio
from dataclasses import dataclass, field
from listener.claude.runner import run_claude_session

CHAT_SYSTEM_PROMPT = """\
You are a meeting assistant. You answer questions about a specific meeting \
based ONLY on the transcript provided.

Rules:
- Answer in the same language the user asks in.
- Reference specific timestamps [HH:MM:SS] when citing the transcript.
- If the answer is not in the transcript, say so explicitly.
- Be concise but thorough.
- For follow-up questions, use the conversation history for context."""


@dataclass
class ChatSession:
    transcript: str
    history: list[dict] = field(default_factory=list)
    max_history_turns: int = 20

    def build_prompt(self, user_message: str) -> str:
        parts = [f"## Meeting Transcript\n\n{self.transcript}\n\n---\n"]
        if self.history:
            parts.append("## Conversation History\n")
            for msg in self.history[-self.max_history_turns:]:
                role = "User" if msg["role"] == "user" else "Assistant"
                parts.append(f"**{role}:** {msg['content']}\n")
            parts.append("---\n")
        parts.append(f"## Current Question\n\n{user_message}")
        return "\n".join(parts)

    async def ask(self, user_message: str, model: str = "claude-sonnet-4-5") -> str:
        prompt = self.build_prompt(user_message)
        response = await run_claude_session(
            prompt=prompt,
            system_prompt=CHAT_SYSTEM_PROMPT,
            model=model,
            node_name="chat_with_transcript",
        )
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": response})
        return response

    def ask_sync(self, user_message: str, model: str = "claude-sonnet-4-5") -> str:
        return asyncio.run(self.ask(user_message, model=model))
