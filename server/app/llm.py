import os
from typing import Protocol

import httpx


class LLM(Protocol):
    def chat(self, system: str, user: str) -> str: ...


class OpenAICompatible:
    """OpenAI, vLLM 등 /chat/completions 호환 서버"""

    def __init__(self):
        self.base = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.key = os.environ["LLM_API_KEY"]
        self.model = os.environ["LLM_MODEL"]
        self.client = httpx.Client(timeout=120)

    def chat(self, system: str, user: str) -> str:
        r = self.client.post(
            f"{self.base}/chat/completions",
            headers={"Authorization": f"Bearer {self.key}"},
            json={
                "model": self.model,
                "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
