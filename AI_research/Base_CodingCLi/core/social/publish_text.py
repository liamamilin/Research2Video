"""The exact text composed by the current platform uploaders, before any mutation."""
from __future__ import annotations


def count(text: str, metric: str = "codepoint") -> int:
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2 if metric == "utf16" else len(text)


def combined_text(platform: str, title: str, description: str, tags: list[str]) -> str:
    if platform == "kuaishou":
        caption = title if not description.strip() or description.strip() == title.strip() else f"{title}\n\n{description}"
        return caption + "".join(f"\n#{tag} " for tag in tags)
    if platform == "douyin":
        return description + " " + " ".join("#" + tag for tag in tags)
    if platform == "weixin_channels":
        return title.strip() + "\n" + "".join(f"#{tag} " for tag in tags) + "\n" + description
    return description


def default_comment(value: dict | None) -> dict:
    """An empty saved template is not a request to send a comment; explicit drafts are kept separately."""
    result = {"enabled": False, "text": "", "pin": False, **(value or {})}
    if not result["text"].strip():
        result.update(enabled=False, pin=False)
    return result


def comment_options(value: dict) -> dict:
    """Text supplied without an enable flag requests a comment; an explicit False is preserved."""
    result = {'enabled': False, 'text': '', 'pin': False, **value}
    if 'enabled' not in value and isinstance(value.get('text'), str):
        result['enabled'] = bool(value['text'].strip())
    return result
