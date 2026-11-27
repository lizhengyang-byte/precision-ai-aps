"""AI 决策层之四：可选的大模型增强（OpenAI 兼容接口）。"""

from __future__ import annotations

import json
import os

import httpx

BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
API_KEY = os.getenv("LLM_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", "deepseek-chat")


def available() -> bool:
    return bool(API_KEY)


def chat(messages: list[dict], temperature: float = 0.2, timeout: float = 20.0) -> str | None:
    if not available():
        return None
    try:
        resp = httpx.post(
            f"{BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL, "messages": messages, "temperature": temperature},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def narrate(payload: dict, question: str) -> str | None:
    """把结构化指标交给模型润色，失败则返回 None 由调用方回退。"""
    system = (
        "你是精密加工车间的排产计划员助手。只能依据给出的JSON数据回答，"
        "不得编造订单、设备或数字；用简洁中文，先给结论再给依据。"
    )
    return chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"问题：{question}\n数据：{json.dumps(payload, ensure_ascii=False)}"},
        ]
    )
