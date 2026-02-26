#  Orchestration Engine - Tool Registry
#
#  Injectable tool registry replacing the global _REGISTRY pattern.
#  Tools are instantiated and registered when the registry is created.
#  Individual tool failures are logged but don't prevent other tools from loading.
#
#  Depends on: tools/base.py, tools/*
#  Used by:    container.py, services/executor.py

import logging

import httpx

from backend.tools.base import Tool

logger = logging.getLogger("orchestration.tools.registry")


class ToolRegistry:
    """Registry of tools available to task executors.

    Replaces the old global _REGISTRY + register() pattern with an
    injectable class for clean testing and lifecycle management.

    Accepts an optional shared httpx.AsyncClient for connection pooling
    across all HTTP-based tools.
    """

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._tools: dict[str, Tool] = {}
        self._http_client = http_client
        self._failed: list[str] = []
        self._register_defaults()

    def _register_defaults(self):
        """Register all built-in tools. Failed tools are logged and skipped.

        Imports are deferred into each factory so that a broken module
        (e.g., missing numpy) only takes down its own tools, not all of them.
        """
        # Shared RAG index cache — both search and lookup share the same indexes.
        # Created lazily on first RAG tool registration so a broken RAG module
        # doesn't prevent non-RAG tools from loading.
        rag_cache = None

        def _rag_cache():
            nonlocal rag_cache
            if rag_cache is None:
                from backend.tools.rag import RAGIndexCache
                rag_cache = RAGIndexCache()
            return rag_cache

        def _search_knowledge():
            from backend.tools.rag import SearchKnowledgeTool
            return SearchKnowledgeTool(cache=_rag_cache(), http_client=self._http_client)

        def _lookup_type():
            from backend.tools.rag import LookupTypeTool
            return LookupTypeTool(cache=_rag_cache())

        def _local_llm():
            from backend.tools.ollama import LocalLLMTool
            return LocalLLMTool(http_client=self._http_client)

        def _generate_image():
            from backend.tools.comfyui import GenerateImageTool
            return GenerateImageTool(http_client=self._http_client)

        def _read_file():
            from backend.tools.file import ReadFileTool
            return ReadFileTool()

        def _write_file():
            from backend.tools.file import WriteFileTool
            return WriteFileTool()

        tool_factories = [
            ("SearchKnowledgeTool", _search_knowledge),
            ("LookupTypeTool", _lookup_type),
            ("LocalLLMTool", _local_llm),
            ("GenerateImageTool", _generate_image),
            ("ReadFileTool", _read_file),
            ("WriteFileTool", _write_file),
        ]

        for name, factory in tool_factories:
            try:
                tool = factory()
                self._tools[tool.name] = tool
            except Exception as e:
                logger.warning("Failed to register tool %s: %s", name, e)
                self._failed.append(name)

        logger.info("Registered %d/%d tools", len(self._tools), len(tool_factories))

    @property
    def failed_tools(self) -> list[str]:
        """Tool class names that failed to register during startup."""
        return list(self._failed)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_many(self, names: list[str]) -> list[Tool]:
        """Get multiple tools by name. Skips unknown names."""
        return [self._tools[n] for n in names if n in self._tools]

    def all_names(self) -> list[str]:
        return list(self._tools.keys())
