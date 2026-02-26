#  Orchestration Engine - Tool Registry
#
#  Injectable tool registry replacing the global _REGISTRY pattern.
#  Tools are instantiated and registered when the registry is created.
#
#  Depends on: tools/base.py, tools/*
#  Used by:    container.py, services/executor.py

import httpx

from backend.tools.base import Tool


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
        self._register_defaults()

    def _register_defaults(self):
        """Register all built-in tools."""
        from backend.tools.rag import SearchKnowledgeTool, LookupTypeTool, RAGIndexCache
        from backend.tools.ollama import LocalLLMTool
        from backend.tools.comfyui import GenerateImageTool
        from backend.tools.file import ReadFileTool, WriteFileTool

        # Shared RAG index cache — both search and lookup share the same indexes
        rag_cache = RAGIndexCache()

        for tool in [
            SearchKnowledgeTool(cache=rag_cache, http_client=self._http_client),
            LookupTypeTool(cache=rag_cache),
            LocalLLMTool(http_client=self._http_client),
            GenerateImageTool(http_client=self._http_client),
            ReadFileTool(),
            WriteFileTool(),
        ]:
            self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_many(self, names: list[str]) -> list[Tool]:
        """Get multiple tools by name. Skips unknown names."""
        return [self._tools[n] for n in names if n in self._tools]

    def all_names(self) -> list[str]:
        return list(self._tools.keys())
