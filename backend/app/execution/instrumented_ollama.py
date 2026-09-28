"""Measure Ollama's raw stream before LangChain parses structured responses."""

import json
import logging
from typing import Any, AsyncIterator, Mapping

from langchain_core.messages import BaseMessage
from langchain_ollama import ChatOllama

from app.shared.ollama_timing import active_ollama_turn


logger = logging.getLogger(__name__)


class InstrumentedChatOllama(ChatOllama):
    """Keep normal ChatOllama behavior while timestamping raw API chunks."""

    async def _acreate_chat_stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Mapping[str, Any] | str]:
        turn = active_ollama_turn.get()
        if turn is None:
            async for chunk in super()._acreate_chat_stream(messages, stop, **kwargs):
                yield chunk
            return

        timing = turn.begin_request(str(kwargs.get("model") or self.model))
        failed = False
        try:
            async for chunk in super()._acreate_chat_stream(messages, stop, **kwargs):
                timing.observe(chunk)
                yield chunk
        except Exception:
            failed = True
            raise
        finally:
            timing.finish(failed=failed)
            logger.info(
                "ollama_raw_timing %s",
                json.dumps({"turn_id": turn.turn_id, **timing.snapshot()}),
            )
