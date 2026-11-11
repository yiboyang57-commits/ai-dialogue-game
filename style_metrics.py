"""文风统计：只观察文本，不改写叙事。"""
import re
from collections import Counter

CLICHES = [r"眸", r"眼中闪过", r"嘴角(?:勾起|上扬|微扬)", r"一丝", r"一抹",
           r"似乎", r"仿佛", r"宛如", r"不易察觉", r"难以(?:言明|名状|言喻)",
           r"意味深长", r"若有所思", r"微微一(?:怔|愣|顿)", r"深吸一口气",
           r"空气(?:仿佛)?凝固", r"心头(?:一|掠过)", r"千言万语"]
DIALOGUE_WINDOW = 15
_PATTERN = re.compile("|".join(CLICHES))
_DIALOGUE = re.compile(r'「[^」]*」|“[^”]*”|"[^"\n]*"')


def cliche_counts(text):
    return Counter(m.group() for m in _PATTERN.finditer(text or ""))


def cliche_rate(text):
    """每千个非空白字符的套话命中次数（重叠词不重复计数）。"""
    size = len(re.sub(r"\s", "", text or ""))
    return 1000 * sum(cliche_counts(text).values()) / size if size else 0.0


def avg_sentence_len(text):
    parts = [re.sub(r"\s", "", p).strip('「」“”"') for p in re.split(r"[。！？!?]+", text or "")]
    parts = [p for p in parts if p]
    return sum(map(len, parts)) / len(parts) if parts else 0.0


def dialogue_tag_rate(text):
    """台词两侧 15 字范围内出现套话的台词比例，不计台词内部。"""
    text = text or ""
    quotes = list(_DIALOGUE.finditer(text))
    marked = 0
    for i, quote in enumerate(quotes):
        left = max(quote.start() - DIALOGUE_WINDOW, quotes[i-1].end() if i else 0)
        right = min(quote.end() + DIALOGUE_WINDOW, quotes[i+1].start() if i+1 < len(quotes) else len(text))
        marked += bool(_PATTERN.search(text[left:quote.start()] + text[quote.end():right]))
    return marked / len(quotes) if quotes else 0.0


def report(texts):
    texts = list(texts)
    text = "\n".join(texts)
    # 分回合统计邻接关系，避免把上一回合末尾误算成下一回合台词的神态标签。
    quotes = [len(_DIALOGUE.findall(t)) for t in texts]
    return {"narratives": len(texts), "cliche_rate": cliche_rate(text),
            "avg_sentence_len": avg_sentence_len(text),
            "dialogue_tag_rate": sum(dialogue_tag_rate(t)*n for t,n in zip(texts, quotes))/sum(quotes) if sum(quotes) else 0.0,
            "top_cliches": cliche_counts(text).most_common(10)}
