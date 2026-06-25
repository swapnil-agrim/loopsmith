"""Flat goal-frontmatter parsing (zero-dep). Frontmatter = a leading ---\n key: value ... \n--- block."""
import re

_FENCE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def parse(text):
    m = _FENCE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"')
    return out


def get(text, key):
    return parse(text).get(key)


def set_field(text, key, value):
    m = _FENCE.match(text)
    if not m:
        raise ValueError("no frontmatter to update")
    block = m.group(1)
    line_re = re.compile(rf"^{re.escape(key)}:.*$", re.MULTILINE)
    new_line = f"{key}: {value}"
    block2 = line_re.sub(new_line, block) if line_re.search(block) else block + f"\n{new_line}"
    return text[:m.start(1)] + block2 + text[m.end(1):]
