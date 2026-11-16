#!/usr/bin/env python3
"""
Build a search-keyword list from our own data instead of a fixed hand-made list.

Every word (and two-word phrase) that appears in many channel titles/bios and ad texts in
data/channels.json and data/ads/*.json becomes a search term, ranked by how many different
channels/ads use it. Function words and Telegram boilerplate are dropped. Terms already in
data/channel_keywords.txt are not repeated.

Output: data/channel_keywords_auto.txt (one term per line, most common first). Collectors read
it after channel_keywords.txt. Re-run as the database grows: new fields surface on their own.
"""
import collections
import glob
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "channel_keywords_auto.txt"
MANUAL = DATA / "channel_keywords.txt"
MAX_TERMS = 1500
MIN_DF_WORD, MIN_DF_PHRASE = 8, 12

STOP_FA = set("""
و در به از که این را با است برای آن یک تا هم بر یا اما اگر هر می نمی شود شد شده کرد کنید کنیم کند کنند کرده
بود باشد باشید هست نیست ما شما او ایشان آنها اینجا آنجا همه هیچ چه چرا چون پس بعد قبل روی زیر بین دیگر خود
خیلی بسیار فقط حتی نیز ولی تو من دارد دارند داریم دارید داشته کار وجود بیشتر کمتر چند همین همان چنین اینکه
جهت طریق توسط همراه درباره مورد موارد داخل بیرون کل جدید روز امروز فردا الان اکنون سال ماه هفته ساعت دقیقه
عضو اعضا کانال کانالها کانال‌ها گروه لینک پیام پیوی ادمین تماس ارتباط جوین عضویت بزنید کلیک اینجا رایگان ویژه
بهترین برترین اولین معتبر رسمی مجموعه صفحه پست پستها آیدی آی‌دی تلگرام اینستاگرام واتساپ سایت وبسایت
برای‌ اونم اینم ها های هایی ای ام ات اش مون تون شون
بدون ترین گذاری بگیر بگیرید شروع دنبال دریافت ثبت کیفیت تست ارسال پشتیبانی روزانه فوری کنی کنه میشه نداره
بیشتری بهتر عالی کامل واقعی سریع سرعت تضمینی تضمین هدیه تخفیف‌ قسمت شماره عنوان توضیحات اطلاعات
""".split())
STOP_EN = set("""
the and for you your with from this that are our all new join channel group telegram link admin contact
official best free more here click now only just get will can not has have www http https com org net
""".split())

PERSIAN_WORD = re.compile(r"[؀-ۿ]{3,}")
LATIN_WORD = re.compile(r"(?<![@\w])[A-Za-z][A-Za-z0-9]{2,20}(?![\w@])")


def normalize(text):
    t = (text or "").replace("ي", "ی").replace("ك", "ک").replace("‌", " ").replace("ـ", "")
    t = re.sub(r"https?://\S+|t\.me/\S+|@\w+", " ", t)
    return t


def tokens(text):
    t = normalize(text)
    words = [w for w in PERSIAN_WORD.findall(t) if w not in STOP_FA]
    words += [w.lower() for w in LATIN_WORD.findall(t) if w.lower() not in STOP_EN and not w.isdigit()]
    return words


def documents():
    ch = json.loads((DATA / "channels.json").read_text("utf-8"))
    for r in ch["rows"]:
        c = dict(zip(ch["fields"], r))
        yield f"{c.get('t') or ''} {c.get('d') or ''}"
    for f in glob.glob(str(DATA / "ads" / "*.json")):
        for a in json.load(open(f, encoding="utf-8")):
            yield f"{a.get('title') or ''} {a.get('text') or ''}"


def main():
    df_word, df_phrase = collections.Counter(), collections.Counter()
    n = 0
    for doc in documents():
        n += 1
        words = tokens(doc)
        df_word.update(set(words))
        df_phrase.update({f"{a} {b}" for a, b in zip(words, words[1:]) if a != b})
    manual = set()
    if MANUAL.exists():
        manual = {normalize(l.split("#", 1)[0]).strip() for l in MANUAL.read_text("utf-8").splitlines()}
    terms = [(w, c) for w, c in df_word.items() if c >= MIN_DF_WORD]
    terms += [(p, c) for p, c in df_phrase.items() if c >= MIN_DF_PHRASE]
    terms = [(t, c) for t, c in terms if t not in manual and len(t) <= 40]
    terms.sort(key=lambda tc: -tc[1])
    terms = terms[:MAX_TERMS]
    header = (f"# Auto-built from {n:,} channel titles/bios and ad texts in our database "
              f"(scripts/build_keywords.py). Most common first; terms in channel_keywords.txt are not repeated.\n")
    OUT.write_text(header + "\n".join(t for t, _ in terms) + "\n", "utf-8")
    print(f"{len(terms)} terms from {n} documents -> {OUT.name}")
    print("top 40:", ", ".join(f"{t}({c})" for t, c in terms[:40]))


if __name__ == "__main__":
    main()
