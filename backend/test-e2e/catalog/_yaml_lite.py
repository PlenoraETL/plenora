"""Restricted YAML parser tailored to the plenora catalog dialect.

PyYAML 6.0.3 + Python 3.14 has a known interpreter instability that
causes sporadic TypeError ('SafeLoader' is not iterable / 'int' + 'SafeLoader')
or segfault when parsing many files in a loop. This module provides a
zero-dependency parser sufficient for our catalog files, which are emitted
in a strict format by `render.py` itself.

Supported syntax:
- Block mappings  (key: value)
- Block sequences (- item)
- Plain scalars   (unquoted, single-line)
- Single-quoted scalars with `''` escape
- Literal block scalars (`|` then indented lines)
- Empty mapping `{}` / empty sequence `[]` (inline only)
- `true` / `false` / `null` (rare, but supported for completeness)
- `#` comments (line-leading or trailing on a key line)

NOT supported (intentionally):
- Anchors (`&`), aliases (`*`)
- Tags (`!!`)
- Flow-style maps `{a: b}` other than empty `{}`
- Flow-style sequences `[a, b]` other than empty `[]`
- Double-quoted scalars with escape sequences

The parser is deterministic and side-effect free.
"""
from __future__ import annotations

from typing import Any


def _strip_comment(line: str) -> str:
    """Remove a trailing `# ...` comment if it's outside any quoted region."""
    in_single = False
    out = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "'":
            if in_single and i + 1 < len(line) and line[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_single = not in_single
            out.append(ch)
        elif ch == "#" and not in_single:
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _parse_scalar(s: str) -> Any:
    s = s.strip()
    if not s:
        return ""
    # Single-quoted
    if s.startswith("'") and s.endswith("'") and len(s) >= 2:
        inner = s[1:-1]
        return inner.replace("''", "'")
    # Double-quoted (basic, no escape sequences)
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        return s[1:-1]
    if s == "null" or s == "~":
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    # int?
    if s.lstrip("-").isdigit():
        try:
            return int(s)
        except ValueError:
            pass
    # float?
    try:
        return float(s)
    except ValueError:
        pass
    return s  # plain string


def _indent_of(line: str) -> int:
    n = 0
    for ch in line:
        if ch == " ":
            n += 1
        else:
            break
    return n


class _Lines:
    def __init__(self, raw: str) -> None:
        # Keep raw lines (preserving leading whitespace) but strip trailing CR.
        self._lines = raw.splitlines()
        self._i = 0

    def peek(self) -> str | None:
        if self._i >= len(self._lines):
            return None
        return self._lines[self._i]

    def advance(self) -> None:
        self._i += 1

    def at_end(self) -> bool:
        return self._i >= len(self._lines)


def _is_blank_or_comment(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("#")


def _parse_block(lines: _Lines, indent: int) -> Any:
    """Parse a block (mapping or sequence) at the given minimum indent level.

    Returns the parsed structure. Stops when a non-blank line with indent < `indent`
    is reached (caller-managed).
    """
    # Skip blanks/comments
    while not lines.at_end():
        line = lines.peek()
        assert line is not None
        if _is_blank_or_comment(line):
            lines.advance()
            continue
        break

    if lines.at_end():
        return None

    line = lines.peek()
    assert line is not None
    cur_indent = _indent_of(line)
    if cur_indent < indent:
        return None

    stripped = line[cur_indent:]
    if stripped.startswith("- "):
        return _parse_sequence(lines, cur_indent)
    if stripped == "-":
        return _parse_sequence(lines, cur_indent)
    return _parse_mapping(lines, cur_indent)


def _parse_mapping(lines: _Lines, indent: int) -> dict:
    out: dict = {}
    while not lines.at_end():
        line = lines.peek()
        assert line is not None
        if _is_blank_or_comment(line):
            lines.advance()
            continue
        cur = _indent_of(line)
        if cur < indent:
            break
        if cur > indent:
            # Shouldn't happen at the start of a mapping key
            break
        body = _strip_comment(line[cur:])
        if not body:
            lines.advance()
            continue
        if body.startswith("- "):
            # we accidentally entered a sequence at this level
            break
        # Find ':' that's not inside a single-quoted string
        key, sep, rest = _split_key(body)
        if sep is None:
            # not a key:value line; abort
            break
        key = key.strip()
        rest = rest.strip()
        lines.advance()
        if rest == "":
            # nested block
            value = _parse_nested(lines, indent)
            out[key] = value
        elif rest == "|":
            value = _parse_literal_block(lines, indent)
            out[key] = value
        elif rest == "[]":
            out[key] = []
        elif rest == "{}":
            out[key] = {}
        else:
            out[key] = _parse_scalar(rest)
    return out


def _split_key(body: str) -> tuple[str, str | None, str]:
    """Split `key: value` honoring single-quoted regions."""
    in_single = False
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "'":
            if in_single and i + 1 < len(body) and body[i + 1] == "'":
                i += 2
                continue
            in_single = not in_single
        elif ch == ":" and not in_single:
            # ensure this is a "key:" boundary (followed by space, EOL, or list start)
            if i + 1 == len(body) or body[i + 1] in (" ", "\t"):
                return body[:i], ":", body[i + 1 :]
        i += 1
    return body, None, ""


def _parse_nested(lines: _Lines, parent_indent: int) -> Any:
    """Parse a child block whose indent must be > parent_indent."""
    while not lines.at_end():
        line = lines.peek()
        assert line is not None
        if _is_blank_or_comment(line):
            lines.advance()
            continue
        break
    if lines.at_end():
        return None
    line = lines.peek()
    assert line is not None
    cur = _indent_of(line)
    if cur <= parent_indent:
        # No nested block — empty value
        return None
    stripped = line[cur:]
    if stripped.startswith("- ") or stripped == "-":
        return _parse_sequence(lines, cur)
    return _parse_mapping(lines, cur)


def _parse_sequence(lines: _Lines, indent: int) -> list:
    out: list = []
    while not lines.at_end():
        line = lines.peek()
        assert line is not None
        if _is_blank_or_comment(line):
            lines.advance()
            continue
        cur = _indent_of(line)
        if cur < indent:
            break
        if cur > indent:
            break
        body = line[cur:]
        if not (body.startswith("- ") or body == "-"):
            break
        # Strip the leading "- "
        if body == "-":
            rest = ""
        else:
            rest = body[2:]
        rest_stripped = _strip_comment(rest).rstrip()
        lines.advance()
        if rest_stripped == "":
            # Nested item (mapping or sequence) on next lines
            child = _parse_nested(lines, indent)
            out.append(child)
        elif rest_stripped == "|":
            out.append(_parse_literal_block(lines, indent))
        elif rest_stripped == "[]":
            out.append([])
        elif rest_stripped == "{}":
            out.append({})
        else:
            # Could be:
            #   - 'scalar'
            #   - key: value          (start of a mapping)
            # Detect by presence of ': ' at top level.
            key, sep, after = _split_key(rest_stripped)
            if sep is not None and key.strip() and not key.strip().startswith("'"):
                # It's an inline mapping start. Treat the dash position as the
                # mapping's indent (cur + 2) and re-parse.
                # Build a synthetic first line at indent+2 and prepend it back
                # so we can reuse _parse_mapping.
                # Simpler: parse this single line as a 1-key mapping seed,
                # then continue parsing siblings with same indent+2.
                inline_key = key.strip()
                inline_rest = after.strip()
                child: dict = {}
                if inline_rest == "":
                    child[inline_key] = _parse_nested(lines, indent + 1)
                elif inline_rest == "|":
                    child[inline_key] = _parse_literal_block(lines, indent + 1)
                elif inline_rest == "[]":
                    child[inline_key] = []
                elif inline_rest == "{}":
                    child[inline_key] = {}
                else:
                    child[inline_key] = _parse_scalar(inline_rest)
                # Now consume any continuation mapping lines at indent + 2.
                tail = _continue_mapping(lines, indent + 2)
                child.update(tail)
                out.append(child)
            else:
                out.append(_parse_scalar(rest_stripped))
    return out


def _continue_mapping(lines: _Lines, indent: int) -> dict:
    """Continue parsing a mapping that may have more keys at exactly `indent`."""
    out: dict = {}
    while not lines.at_end():
        line = lines.peek()
        assert line is not None
        if _is_blank_or_comment(line):
            lines.advance()
            continue
        cur = _indent_of(line)
        if cur < indent:
            break
        if cur > indent:
            # belongs to last key — but we don't have a "last key" pointer here.
            # Bail; caller handles the over-indent via _parse_nested.
            break
        body = _strip_comment(line[cur:])
        if not body or body.startswith("- "):
            break
        key, sep, rest = _split_key(body)
        if sep is None:
            break
        key = key.strip()
        rest = rest.strip()
        lines.advance()
        if rest == "":
            out[key] = _parse_nested(lines, indent)
        elif rest == "|":
            out[key] = _parse_literal_block(lines, indent)
        elif rest == "[]":
            out[key] = []
        elif rest == "{}":
            out[key] = {}
        else:
            out[key] = _parse_scalar(rest)
    return out


def _parse_literal_block(lines: _Lines, parent_indent: int) -> str:
    """Parse a `|`-style literal block. All indented lines belong to it."""
    block_lines: list[str] = []
    block_indent: int | None = None
    while not lines.at_end():
        line = lines.peek()
        assert line is not None
        if line.strip() == "":
            block_lines.append("")
            lines.advance()
            continue
        cur = _indent_of(line)
        if cur <= parent_indent:
            break
        if block_indent is None:
            block_indent = cur
        # strip the block's base indent
        block_lines.append(line[block_indent:])
        lines.advance()
    # Trim trailing empty lines
    while block_lines and block_lines[-1] == "":
        block_lines.pop()
    return "\n".join(block_lines)


def safe_load(text: str) -> Any:
    """Parse a YAML document (in our restricted dialect) and return the root value."""
    lines = _Lines(text)
    return _parse_block(lines, indent=0)
