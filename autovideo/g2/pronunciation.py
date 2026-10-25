"""How the Kokoro voice is told to read a script, without changing a word of it.

The script text is never touched: captions, the exact-text check and the narration plan keep the author's
words. Only the phonemes sent to the voice change, in two ways the Chinese text-to-phoneme step gets wrong:

* Polyphonic characters. pypinyin picks one reading per word from a dictionary, and for technical writing it
  is often the wrong one: 第一行 read "xíng" instead of "háng", 重存 / 重写 read "zhòng" instead of "chóng",
  相差 read "chà" instead of "chā", 就得 read "dé" instead of "děi". ``PHRASES`` pins the reading of a word;
  ``DEFAULT_READINGS`` changes a character's reading everywhere the dictionary has no better answer.
* Latin words. misaki's Chinese step leaves them as letters, which the voice cannot pronounce. They are turned
  into English phonemes with espeak instead (``token``, ``TTL``, ``Opus``).

``unreviewed_readings`` lists the polyphonic characters of a script that are not pinned, with the reading the
voice will use, so a person can check them before listening to the whole audio.
"""

from __future__ import annotations

import re

#: word -> its syllables, with tone marks. Added to jieba too, so the word is not split.
PHRASES: dict[str, list[str]] = {
    # 行 háng: a line, a row, a trade
    "第一行": ["dì", "yī", "háng"], "一行": ["yī", "háng"], "每一行": ["měi", "yī", "háng"], "这一行": ["zhè", "yī", "háng"],
    "那一行": ["nà", "yī", "háng"], "哪一行": ["nǎ", "yī", "háng"], "最后一行": ["zuì", "hòu", "yī", "háng"],
    "多行": ["duō", "háng"], "单行": ["dān", "háng"], "首行": ["shǒu", "háng"], "末行": ["mò", "háng"],
    "行首": ["háng", "shǒu"], "行尾": ["háng", "wěi"], "行号": ["háng", "hào"], "行数": ["háng", "shù"],
    "每行": ["měi", "háng"], "换行": ["huàn", "háng"],
    # 重 chóng: again
    "重存": ["chóng", "cún"], "重写": ["chóng", "xiě"], "重算": ["chóng", "suàn"], "重试": ["chóng", "shì"],
    "重启": ["chóng", "qǐ"], "重跑": ["chóng", "pǎo"], "重发": ["chóng", "fā"], "重读": ["chóng", "dú"],
    "重放": ["chóng", "fàng"], "重建": ["chóng", "jiàn"], "重做": ["chóng", "zuò"], "重来": ["chóng", "lái"],
    "重组": ["chóng", "zǔ"], "重构": ["chóng", "gòu"], "重置": ["chóng", "zhì"], "重载": ["chóng", "zǎi"],
    "重新写": ["chóng", "xīn", "xiě"], "重新存": ["chóng", "xīn", "cún"],
    # 得 děi: must
    "就得": ["jiù", "děi"], "都得": ["dōu", "děi"], "还得": ["hái", "děi"], "也得": ["yě", "děi"], "得重": ["děi", "chóng"],
    "得补": ["děi", "bǔ"], "得核实": ["děi", "hé", "shí"], "得先": ["děi", "xiān"], "得看": ["děi", "kàn"],
    "得算": ["děi", "suàn"], "得留": ["děi", "liú"], "得换": ["děi", "huàn"], "得把": ["děi", "bǎ"],
    # 差 chā: a difference; chà only in 差不多 / 差点 / 差劲
    "差不多": ["chà", "bù", "duō"], "差点": ["chà", "diǎn"], "差劲": ["chà", "jìn"], "出差": ["chū", "chāi"],
    "相差": ["xiāng", "chā"], "差价": ["chā", "jià"], "差别": ["chā", "bié"], "差异": ["chā", "yì"],
    "差距": ["chā", "jù"], "总账差": ["zǒng", "zhàng", "chā"], "价差": ["jià", "chā"], "时差": ["shí", "chā"],
    # 调 diào: call
    "调用": ["diào", "yòng"], "乱调": ["luàn", "diào"], "调度": ["diào", "dù"],
    # 数 shù / 长 cháng / 还 hái
    "次数": ["cì", "shù"], "数字": ["shù", "zì"], "数据": ["shù", "jù"], "参数": ["cān", "shù"],
}

#: character -> readings, the first being the new default for that character.
DEFAULT_READINGS: dict[str, str] = {
    "差": "chā,chà,chāi,cī",
}

_installed = False


def install() -> None:
    """Pin the readings above in pypinyin and keep the words whole in jieba (once)."""
    global _installed
    if _installed:
        return
    import jieba
    import pypinyin

    pypinyin.load_single_dict({ord(char): readings for char, readings in DEFAULT_READINGS.items()})
    pypinyin.load_phrases_dict({word: [[syllable] for syllable in syllables] for word, syllables in PHRASES.items()})
    for word in PHRASES:
        jieba.add_word(word, freq=1_000_000)
    _installed = True


_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9'\-]*(?:\s+[A-Za-z][A-Za-z0-9'\-]*)*")


def split_latin(text: str) -> list[tuple[bool, str]]:
    """[(is_latin, piece)] in order; a run of Latin words (with the spaces between them) is one piece."""
    pieces, position = [], 0
    for match in _LATIN.finditer(text):
        if match.start() > position:
            pieces.append((False, text[position:match.start()]))
        pieces.append((True, match.group(0)))
        position = match.end()
    if position < len(text):
        pieces.append((False, text[position:]))
    return pieces


def phonemes(text: str, g2p, english, *, flat_questions: bool = False) -> str:
    """Phonemes for ``text``: Chinese through ``g2p``, Latin runs through ``english`` (a callable on a string).

    ``flat_questions`` reads a question mark as a full stop. The Chinese voices turn a trailing "?" into an
    odd rising tail on the last syllable.
    """
    install()
    if flat_questions:
        text = text.replace("？", "。").replace("?", ".")
    parts = []
    for is_latin, piece in split_latin(text):
        if not piece.strip():
            continue
        if is_latin:
            parts.append(english(piece.strip()))
        else:
            parts.append(g2p(piece)[0])
    return " ".join(part for part in parts if part)


def english_phonemes():
    """A callable turning an English word or acronym into phonemes the voice knows (espeak, American)."""
    from misaki import espeak

    engine = espeak.EspeakG2P(language="en-us")
    return lambda word: engine(word)[0]


def unreviewed_readings(text: str) -> list[dict]:
    """Polyphonic characters in ``text`` whose reading is chosen by the dictionary, not pinned here.

    [{"char", "word", "reading", "other_readings"}], once per character and word. Only characters with
    more than one common reading are listed, so a person can scan them for a wrong pick.
    """
    install()
    import jieba
    import pypinyin
    from pypinyin import Style

    found, seen = [], set()
    for word in jieba.lcut(text):
        if not re.fullmatch(r"[一-鿿]+", word) or word in PHRASES:
            continue
        chosen = pypinyin.lazy_pinyin(word, style=Style.TONE)
        for char, reading in zip(word, chosen):
            options = {r for r in pypinyin.pinyin(char, style=Style.TONE, heteronym=True)[0]}
            common = options & COMMON_POLYPHONES.get(char, set())
            if len(common) > 1 and (char, word) not in seen:
                seen.add((char, word))
                found.append({"char": char, "word": word, "reading": reading, "other_readings": sorted(common - {reading})})
    return found


#: The characters whose second reading is common enough to matter in running text; the rest of the dictionary's
#: alternatives (rare variants) would bury the useful ones.
COMMON_POLYPHONES: dict[str, set[str]] = {
    "行": {"xíng", "háng"}, "重": {"zhòng", "chóng"}, "差": {"chā", "chà", "chāi"}, "得": {"dé", "děi", "de"},
    "长": {"cháng", "zhǎng"}, "还": {"hái", "huán"}, "调": {"diào", "tiáo"}, "数": {"shù", "shǔ", "shuò"},
    "为": {"wéi", "wèi"}, "和": {"hé", "hè", "huó", "huò"}, "了": {"le", "liǎo"}, "着": {"zhe", "zhuó", "zháo"},
    "地": {"de", "dì"}, "都": {"dōu", "dū"}, "只": {"zhǐ", "zhī"}, "少": {"shǎo", "shào"}, "便": {"biàn", "pián"},
    "转": {"zhuǎn", "zhuàn"}, "降": {"jiàng", "xiáng"}, "更": {"gèng", "gēng"}, "觉": {"jué", "jiào"},
    "背": {"bèi", "bēi"}, "传": {"chuán", "zhuàn"}, "量": {"liàng", "liáng"}, "角": {"jiǎo", "jué"},
    "处": {"chù", "chǔ"}, "当": {"dāng", "dàng"}, "假": {"jiǎ", "jià"}, "几": {"jǐ", "jī"}, "将": {"jiāng", "jiàng"},
    "倒": {"dào", "dǎo"}, "切": {"qiē", "qiè"}, "累": {"lèi", "lěi", "léi"}, "论": {"lùn", "lún"},
    "没": {"méi", "mò"}, "盛": {"shèng", "chéng"}, "种": {"zhǒng", "zhòng"}, "挑": {"tiāo", "tiǎo"},
}
