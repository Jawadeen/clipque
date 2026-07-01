from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import DEFAULT_HASHTAGS, DEFAULT_OPENROUTER_MODELS, GEMINI_MODELS


def clean_ai_caption(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"(?i)^caption\s*idea\s*:\s*", "", text).strip()
    text = re.sub(r"(?i)^caption\s*:\s*", "", text).strip()
    text = text.replace("\r", "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = " ".join(lines)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_hashtags(tags: str) -> str:
    tags = (tags or "").strip()
    if not tags:
        return " ".join(DEFAULT_HASHTAGS)
    parts = re.split(r"[ ,\n\t]+", tags)
    cleaned: list[str] = []
    lower_seen: set[str] = set()
    for part in parts:
        part = part.strip()
        if not part:
            continue
        part = re.sub(r"[^\w#]", "", part)
        if not part:
            continue
        if not part.startswith("#"):
            part = "#" + part
        if part.lower() not in lower_seen:
            lower_seen.add(part.lower())
            cleaned.append(part)
    return " ".join(cleaned[:12]) or " ".join(DEFAULT_HASHTAGS)


@dataclass
class CaptionResult:
    text: str
    provider: str


class CaptionCycler:
    def __init__(self, gemini_key: str, gemini_model: str, openrouter_key: str, openrouter_models: list[str], base_hashtags: str, log_callback=None):
        self.gemini_key = (gemini_key or "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
        self.gemini_model = (gemini_model or "").strip() or GEMINI_MODELS[0]
        self.openrouter_key = (openrouter_key or "").strip() or os.environ.get("OPENROUTER_API_KEY", "").strip()
        self.openrouter_models = [m.strip() for m in openrouter_models if m.strip()] or list(DEFAULT_OPENROUTER_MODELS)
        self.base_hashtags = normalize_hashtags(base_hashtags)
        self.log_callback = log_callback
        self._gemini_client = None

    def log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    def prompt(self, transcript: str) -> str:
        transcript = re.sub(r"\s+", " ", transcript or "").strip()[:4500]
        return f"""
Create ONE TikTok caption with hashtags from this transcript.

Rules:
- Output only the final caption text.
- Do not write labels like "Caption:" or "Caption idea:".
- Keep it natural, short, and scroll-stopping.
- Include 3 to 6 relevant hashtags at the end.
- You may use these base hashtags if relevant: {self.base_hashtags}
- Do not invent facts beyond the transcript.

Transcript:
{transcript}
""".strip()

    def fallback_caption(self, transcript: str, group_number: int) -> str:
        cleaned = re.sub(r"\s+", " ", transcript or "").strip()
        if not cleaned:
            return f"Part {group_number} gets wild fast {self.base_hashtags}"
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        best = max(sentences[:8], key=len, default=cleaned).strip(" \"'")
        if len(best) > 110:
            best = best[:107].rsplit(" ", 1)[0] + "..."
        if not best:
            best = "This moment got way too intense"
        return f"{best} {self.base_hashtags}"

    def get_gemini_client(self):
        if not self.gemini_key:
            return None
        if self._gemini_client is not None:
            return self._gemini_client
        try:
            from google import genai
        except ImportError:
            self.log("google-genai is not installed, skipping Gemini.")
            return None
        self._gemini_client = genai.Client(api_key=self.gemini_key)
        return self._gemini_client

    def try_gemini(self, prompt: str, group_name: str) -> CaptionResult | None:
        client = self.get_gemini_client()
        if client is None:
            return None
        models = [self.gemini_model] + [m for m in GEMINI_MODELS if m != self.gemini_model]
        for model_name in models:
            for attempt in range(1, 4):
                try:
                    response = client.models.generate_content(model=model_name, contents=prompt)
                    text = clean_ai_caption(getattr(response, "text", "") or str(response))
                    if text:
                        return CaptionResult(text=text, provider=f"Gemini:{model_name}")
                except Exception as exc:
                    err_text = str(exc)
                    retryable = any(code in err_text for code in ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "timeout", "Timeout"])
                    if retryable and attempt < 3:
                        delay = 5 * attempt
                        self.log(f"Gemini busy for {group_name} on {model_name}. Retrying in {delay}s...")
                        time.sleep(delay)
                        continue
                    self.log(f"Gemini model failed for {group_name}: {model_name}")
                    break
        return None

    def try_openrouter_model(self, model_name: str, prompt: str, group_name: str) -> CaptionResult | None:
        if not self.openrouter_key:
            return None
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "You write short TikTok captions. Return only the caption text."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 100,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.openrouter_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://clipque.netlify.app/",
                "X-Title": "ClipQue Desktop",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
            parsed = json.loads(body)
            text = parsed["choices"][0]["message"]["content"]
            text = clean_ai_caption(text)
            if text:
                return CaptionResult(text=text, provider=f"OpenRouter:{model_name}")
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8")[:500]
            except Exception:
                detail = str(exc)
            self.log(f"OpenRouter failed for {group_name} on {model_name}: {exc.code} {detail}")
        except Exception as exc:
            self.log(f"OpenRouter failed for {group_name} on {model_name}: {exc}")
        return None

    def try_openrouter(self, prompt: str, group_name: str) -> CaptionResult | None:
        if not self.openrouter_key:
            return None
        for model_name in self.openrouter_models:
            for attempt in range(1, 3):
                result = self.try_openrouter_model(model_name, prompt, group_name)
                if result:
                    return result
                if attempt < 2:
                    time.sleep(4)
        return None

    def generate(self, transcript: str, group_name: str, group_number: int) -> CaptionResult:
        prompt = self.prompt(transcript)
        result = self.try_gemini(prompt, group_name)
        if result:
            return result
        result = self.try_openrouter(prompt, group_name)
        if result:
            return result
        self.log(f"All AI providers failed or unavailable for {group_name}. Using local fallback caption.")
        return CaptionResult(text=self.fallback_caption(transcript, group_number), provider="LocalFallback")
