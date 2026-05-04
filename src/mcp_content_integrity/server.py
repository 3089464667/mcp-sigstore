"""Base64 Auto-Decode MCP Server.

Scans any text for base64-encoded segments and automatically decodes them in-place.
Non-base64 content passes through unchanged.
"""

import asyncio
import base64
import re
import binascii
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("base64-autodecode")

# Match sequences of base64 alphabet chars (including padding =), at least 4 chars.
# Validation is done by _is_valid_base64(), not the regex.
BASE64_PATTERN = re.compile(r'[A-Za-z0-9+/=]{4,}')


def _is_valid_base64(s: str) -> bool:
    """Check if a string is valid base64 and actually decodes to something meaningful."""
    if len(s) < 4:
        return False
    if len(s) % 4 != 0:
        return False
    try:
        decoded = base64.b64decode(s, validate=True)
        if len(decoded) == 0:
            return False
        # Accept if: (a) high printable ASCII ratio, OR (b) valid UTF-8
        printable = sum(1 for b in decoded if 32 <= b < 127 or b in (9, 10, 13))
        if printable / len(decoded) >= 0.5:
            return True
        # Also accept if decodes to valid UTF-8 (handles Chinese, Japanese, etc. in base64)
        try:
            decoded.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False
    except (binascii.Error, ValueError):
        return False


def _find_base64_segments(text: str) -> list[dict]:
    """Find all base64-encoded segments in text."""
    results = []
    for match in BASE64_PATTERN.finditer(text):
        candidate = match.group(0)
        if _is_valid_base64(candidate):
            try:
                decoded_bytes = base64.b64decode(candidate, validate=True)
                # Try UTF-8 first, fall back to latin-1
                try:
                    decoded_text = decoded_bytes.decode("utf-8")
                    encoding = "utf-8"
                except UnicodeDecodeError:
                    decoded_text = decoded_bytes.decode("latin-1")
                    encoding = "latin-1"

                results.append({
                    "start": match.start(),
                    "end": match.end(),
                    "encoded": candidate,
                    "decoded": decoded_text,
                    "encoding": encoding,
                    "byte_length": len(decoded_bytes),
                })
            except Exception:
                continue
    return results


def _decode_replace(text: str, segments: list[dict]) -> str:
    """Replace all base64 segments with their decoded text, working backwards."""
    result = text
    # Process from end to start to preserve positions
    for seg in reversed(segments):
        result = result[:seg["start"]] + seg["decoded"] + result[seg["end"]:]
    return result


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="decode_base64",
            description=(
                "Scan text for base64-encoded content and automatically decode it. "
                "Any base64 strings found are replaced with their decoded plaintext. "
                "Non-base64 content is left unchanged. "
                "Returns both the transformed text and a report of what was decoded. "
                "Use this whenever you encounter text that might contain base64 blobs — "
                "API responses, configuration files, email attachments, encoded payloads, etc."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to scan for base64 and decode",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="detect_base64",
            description=(
                "Detect all base64-encoded segments in text WITHOUT replacing them. "
                "Returns the positions and decoded previews of each base64 blob found. "
                "Use this first if you want to see what's encoded before deciding to decode. "
                "Returns count, positions, and decoded previews."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to scan for base64 segments",
                    },
                },
                "required": ["text"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    match name:
        case "decode_base64":
            return _handle_decode(arguments)
        case "detect_base64":
            return _handle_detect(arguments)
        case _:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]


def _handle_decode(args: dict) -> list[TextContent]:
    text = args["text"]
    segments = _find_base64_segments(text)

    if not segments:
        return [TextContent(
            type="text",
            text=f"[no base64 found] No base64-encoded content detected in the input text ({len(text)} chars). Content returned unchanged."
        )]

    decoded_text = _decode_replace(text, segments)

    report_lines = [f"Decoded {len(segments)} base64 segment(s):"]
    for i, seg in enumerate(segments):
        preview = seg["decoded"][:60].replace("\n", "\\n")
        report_lines.append(
            f"  [{i+1}] position {seg['start']}-{seg['end']}, "
            f"{seg['byte_length']} bytes → {len(seg['decoded'])} chars, "
            f"preview: \"{preview}{'...' if len(seg['decoded']) > 60 else ''}\""
        )

    report = "\n".join(report_lines)
    result = f"{report}\n\n--- DECODED OUTPUT ---\n{decoded_text}"

    return [TextContent(type="text", text=result)]


def _handle_detect(args: dict) -> list[TextContent]:
    text = args["text"]
    segments = _find_base64_segments(text)

    if not segments:
        return [TextContent(
            type="text",
            text=f"No base64-encoded content found in {len(text)} chars of input."
        )]

    import json
    report = []
    for i, seg in enumerate(segments):
        preview = seg["decoded"][:80].replace("\n", "\\n")
        report.append({
            "index": i + 1,
            "position": f"{seg['start']}-{seg['end']}",
            "encoded_length": len(seg["encoded"]),
            "decoded_bytes": seg["byte_length"],
            "decoded_chars": len(seg["decoded"]),
            "encoding": seg["encoding"],
            "preview": preview + ("..." if len(seg["decoded"]) > 80 else ""),
        })

    summary = (
        f"Found {len(segments)} base64 segment(s) in {len(text)} chars.\n"
        f"Use 'decode_base64' to decode them.\n\n"
        f"{json.dumps(report, indent=2, ensure_ascii=False)}"
    )
    return [TextContent(type="text", text=summary)]


def main():
    asyncio.run(stdio_server(server))


if __name__ == "__main__":
    main()
