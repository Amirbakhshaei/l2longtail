from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def _extract_sources_from_json(source: str) -> str:
    """Extract Solidity source from Etherscan multi-file JSON format."""
    stripped = source.strip()
    if stripped.startswith("{{"):
        stripped = stripped[1:]
    if stripped.endswith("}}"):
        stripped = stripped[:-1]
    try:
        data = json.loads(stripped)
        if "sources" in data:
            parts = []
            for file_path, file_data in data["sources"].items():
                content = file_data.get("content", "")
                if content:
                    parts.append(content)
            return "\n".join(parts)
    except (json.JSONDecodeError, AttributeError):
        pass
    return source


def minify_solidity(source: str) -> str:
    original_size = len(source)
    if original_size == 0:
        return source

    code = _extract_sources_from_json(source)

    code = re.sub(r"//[^\n]*", "", code)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    code = re.sub(r"pragma\s+solidity\s+[^;]+;", "", code)
    code = re.sub(r"\b(interface|abstract)\b", "", code)
    code = re.sub(r"\s+", " ", code).strip()

    compressed_size = len(code)
    ratio = (1 - compressed_size / original_size) * 100

    if ratio < 60:
        logger.warning(
            "minifier compression below 60%%: original=%d compressed=%d ratio=%.1f%%",
            original_size,
            compressed_size,
            ratio,
        )

    return code
