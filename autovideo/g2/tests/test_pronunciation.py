"""The voice is told how to read polyphonic characters and Latin words; the script text is never changed."""
import pytest

pypinyin = pytest.importorskip("pypinyin")
jieba = pytest.importorskip("jieba")
misaki_zh = pytest.importorskip("misaki.zh")

from g2 import pronunciation as p  # noqa: E402


def pinyin(word):
    p.install()
    return pypinyin.lazy_pinyin(word, style=pypinyin.Style.TONE)


@pytest.mark.parametrize("word,expected", [
    ("第一行", ["dì", "yī", "háng"]), ("一行", ["yī", "háng"]), ("重存", ["chóng", "cún"]), ("重写", ["chóng", "xiě"]),
    ("就得", ["jiù", "děi"]), ("相差", ["xiāng", "chā"]), ("差不多", ["chà", "bù", "duō"]), ("银行", ["yín", "háng"]),
    ("重要", ["zhòng", "yào"]),  # the dictionary's own answers stay where nobody pinned one
])
def test_pinned_readings_and_the_dictionary_where_nothing_is_pinned(word, expected):
    assert pinyin(word) == expected


def test_the_pinned_words_stay_whole_when_a_sentence_is_cut_into_words():
    p.install()
    assert "第一行" in jieba.lcut("系统提示第一行是当前时间")
    assert "重存" in jieba.lcut("就得每次重存")
    assert pypinyin.lazy_pinyin("差", style=pypinyin.Style.TONE) == ["chā"]  # the new default for the character


def test_latin_runs_are_split_from_chinese_and_keep_their_spaces_between_words():
    assert p.split_latin("Opus 5.5 输入价每百万 token 4 美元") == [
        (True, "Opus"), (False, " 5.5 输入价每百万 "), (True, "token"), (False, " 4 美元")]
    assert p.split_latin("Claude Code 的版本") == [(True, "Claude Code"), (False, " 的版本")]
    assert p.split_latin("全是中文。") == [(False, "全是中文。")]


def test_phonemes_send_english_to_the_english_step_and_chinese_to_the_chinese_step():
    calls = []
    zh = lambda text: calls.append(("zh", text)) or (f"<{text.strip()}>", None)
    en = lambda word: calls.append(("en", word)) or f"[{word}]"
    assert p.phonemes("一个 token 就是", zh, en) == "<一个> [token] <就是>"
    assert ("en", "token") in calls


def test_a_question_mark_can_be_read_flat_without_touching_the_text():
    zh = lambda text: (text, None)
    en = lambda word: word
    text = "为什么强调一整段？"
    assert p.phonemes(text, zh, en) == text
    assert p.phonemes(text, zh, en, flat_questions=True) == "为什么强调一整段。"
    assert text == "为什么强调一整段？"


def test_english_words_become_phonemes_instead_of_letters():
    english = p.english_phonemes()
    assert english("token") != "token" and "k" in english("token")
    assert english("TTL").count("ː") >= 2  # the letters t, t, l, said as names


def test_the_review_lists_unpinned_polyphones_with_the_reading_the_voice_will_use():
    found = p.unreviewed_readings("就得每次重存，还有更多。长度不够，数据很长。")
    words = {(item["char"], item["word"]) for item in found}
    assert ("重", "重存") not in words and ("得", "就得") not in words  # pinned
    assert any(item["char"] == "还" and item["reading"] == "hái" and "huán" in item["other_readings"] for item in found)
    assert any(item["char"] == "长" for item in found)
