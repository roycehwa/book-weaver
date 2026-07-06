from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable

from config import get_settings
from engine_home import resolve_book_weaver_home


class AIBackendUnavailable(RuntimeError):
    pass


class AIOutputError(RuntimeError):
    pass


@dataclass(frozen=True)
class BookOverview:
    book_id: str
    introduction: str
    key_arguments: list[str]
    reading_suggestions: str
    generated_at: str
    model: str


@dataclass(frozen=True)
class ChapterSummary:
    book_id: str
    chapter_index: int
    summary: str
    generated_at: str
    model: str


class AICache:
    def __init__(self, cache_dir: str | Path, ttl_hours: int = 168):
        self.cache_dir = Path(cache_dir)
        self.ttl_hours = ttl_hours
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _path(self, prefix: str, content: str) -> Path:
        import hashlib

        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{prefix}_{digest}.json"

    async def get(self, prefix: str, content: str) -> dict[str, Any] | None:
        path = self._path(prefix, content)
        async with self._lock:
            if not path.exists():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                cached_at = datetime.fromisoformat(payload["cached_at"])
                if datetime.utcnow() - cached_at > timedelta(hours=self.ttl_hours):
                    return None
                data = payload.get("data")
                return data if isinstance(data, dict) else None
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                return None

    async def set(self, prefix: str, content: str, data: dict[str, Any]) -> None:
        path = self._path(prefix, content)
        payload = {"cached_at": datetime.utcnow().isoformat(), "data": data}
        async with self._lock:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    async def clear_expired(self) -> int:
        removed = 0
        async with self._lock:
            for path in self.cache_dir.glob("*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    cached_at = datetime.fromisoformat(payload["cached_at"])
                    if datetime.utcnow() - cached_at > timedelta(hours=self.ttl_hours):
                        path.unlink()
                        removed += 1
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    path.unlink()
                    removed += 1
        return removed


class SharedTranslatorProxy:
    def __init__(self, *, python_path: Path, project_home: Path, backend_name: str):
        self.python_path = python_path
        self.project_home = project_home
        self.name = backend_name

    def translate_chunk(self, chunk, source_language, target_language):
        script = (
            "import sys\n"
            "from pdf_translator.models import TranslationChunk\n"
            "from pdf_translator.translate import build_translator\n"
            "backend, source_language, target_language = sys.argv[1:4]\n"
            "translator = build_translator(backend)\n"
            "text = translator.translate_chunk(\n"
            "    TranslationChunk(index=0, markdown=sys.stdin.read()),\n"
            "    source_language or None,\n"
            "    target_language,\n"
            ")\n"
            "sys.stdout.write(text)\n"
        )
        result = subprocess.run(
            [
                str(self.python_path),
                "-c",
                script,
                self.name,
                source_language or "",
                target_language,
            ],
            cwd=self.project_home,
            input=chunk.markdown,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if result.returncode != 0:
            raise AIBackendUnavailable("AI backend request failed.")
        return result.stdout


def _default_translator_factory(backend_name: str):
    settings = get_settings()
    configured_home = (
        os.getenv("BOOK_WEAVER_HOME")
        or os.getenv("PDF_TRANSLATOR_HOME")
        or settings.BOOK_WEAVER_HOME
        or settings.PDF_TRANSLATOR_HOME
    )
    if not configured_home:
        raise AIBackendUnavailable(
            "AI backend is not configured: set BOOK_WEAVER_HOME and the selected provider credentials."
        )
    project_home = resolve_book_weaver_home(configured=configured_home)
    python_candidates = [
        project_home / ".venv" / "bin" / "python",
        project_home / ".venv" / "Scripts" / "python.exe",
    ]
    python_path = next((path for path in python_candidates if path.exists()), None)
    if python_path is None:
        raise AIBackendUnavailable(
            "AI backend is not configured: pdf-translator virtual environment is missing."
        )
    return SharedTranslatorProxy(
        python_path=python_path,
        project_home=project_home,
        backend_name=backend_name,
    )


class BookmateAIService:
    def __init__(
        self,
        *,
        cache_dir: str | Path | None = None,
        backend_name: str | None = None,
        translator_factory: Callable[[str], Any] | None = None,
    ):
        settings = get_settings()
        self.backend_name = backend_name or settings.BOOKMATE_AI_BACKEND
        self.cache = AICache(
            cache_dir or settings.AI_CACHE_DIR,
            settings.AI_CACHE_TTL_HOURS,
        )
        self._translator_factory = translator_factory or _default_translator_factory
        self._translator = None
        self._semaphore = asyncio.Semaphore(settings.AI_MAX_CONCURRENT)

    def _get_translator(self):
        if self._translator is None:
            try:
                self._translator = self._translator_factory(self.backend_name)
            except AIBackendUnavailable:
                raise
            except Exception as exc:
                raise AIBackendUnavailable(
                    "AI backend is not configured: verify the shared pdf-translator adapter."
                ) from exc
        return self._translator

    async def _complete(self, prompt: str) -> str:
        translator = self._get_translator()
        try:
            from pdf_translator.models import TranslationChunk
        except ImportError:
            @dataclass
            class TranslationChunk:
                index: int
                markdown: str

        chunk = TranslationChunk(index=0, markdown=prompt)
        async with self._semaphore:
            loop = asyncio.get_running_loop()
            try:
                return await loop.run_in_executor(
                    None,
                    lambda: translator.translate_chunk(
                        chunk=chunk,
                        source_language=None,
                        target_language="zh-CN",
                    ),
                )
            except Exception as exc:
                raise AIBackendUnavailable("AI backend request failed.") from exc

    async def generate_book_overview(
        self,
        book_id: str,
        title: str,
        chapters: list[dict[str, Any]],
    ) -> BookOverview:
        cache_key = f"{book_id}|{title}|{len(chapters)}"
        cached = await self.cache.get("overview", cache_key)
        if cached:
            return BookOverview(**cached)

        toc = "\n".join(f"{index + 1}. {chapter['title']}" for index, chapter in enumerate(chapters))
        samples = "\n\n".join(
            f"章节：{chapter['title']}\n{str(chapter.get('content') or '')[:800]}"
            for chapter in chapters[:5]
        )
        response = await self._complete(
            "你是书籍分析助手。请基于目录和章节样本生成中文阅读概览。"
            "只返回 JSON，字段必须为 introduction、key_arguments、reading_suggestions。\n\n"
            f"书名：{title}\n目录：\n{toc}\n\n章节样本：\n{samples}"
        )
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if not match:
            raise AIOutputError("AI backend did not return valid overview JSON.")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AIOutputError("AI backend did not return valid overview JSON.") from exc
        introduction = str(data.get("introduction") or "").strip()
        arguments = data.get("key_arguments")
        suggestions = str(data.get("reading_suggestions") or "").strip()
        if not introduction or not isinstance(arguments, list) or not suggestions:
            raise AIOutputError("AI backend did not return valid overview JSON.")
        overview = BookOverview(
            book_id=book_id,
            introduction=introduction,
            key_arguments=[str(item).strip() for item in arguments if str(item).strip()],
            reading_suggestions=suggestions,
            generated_at=datetime.utcnow().isoformat(),
            model=str(getattr(self._get_translator(), "name", self.backend_name)),
        )
        await self.cache.set("overview", cache_key, asdict(overview))
        return overview

    async def generate_chapter_summary(
        self,
        book_id: str,
        chapter_index: int,
        chapter_title: str,
        chapter_content: str,
    ) -> ChapterSummary:
        cache_key = f"{book_id}|{chapter_index}|{chapter_title}|{chapter_content[:200]}"
        cached = await self.cache.get("summary", cache_key)
        if cached:
            return ChapterSummary(**cached)
        response = (
            await self._complete(
                "你是书籍分析助手。请为以下章节生成 200 至 300 字中文摘要。"
                "只返回摘要正文，不要添加说明。\n\n"
                f"章节标题：{chapter_title}\n\n章节内容：\n{chapter_content[:8000]}"
            )
        ).strip()
        if not response:
            raise AIOutputError("AI backend returned an empty chapter summary.")
        summary = ChapterSummary(
            book_id=book_id,
            chapter_index=chapter_index,
            summary=response,
            generated_at=datetime.utcnow().isoformat(),
            model=str(getattr(self._get_translator(), "name", self.backend_name)),
        )
        await self.cache.set("summary", cache_key, asdict(summary))
        return summary

    async def clear_cache(self) -> int:
        return await self.cache.clear_expired()


_ai_service: BookmateAIService | None = None


async def get_ai_service() -> BookmateAIService:
    global _ai_service
    if _ai_service is None:
        _ai_service = BookmateAIService()
    return _ai_service
