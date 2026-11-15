#!/usr/bin/env python3
"""
Daily status report, delivered by e-mail through GitHub: it opens an issue that mentions the
repository owner (GitHub e-mails mentions), and closes the previous day's report issue.

Contents (Persian): what was added in the last 24 h, totals, ad-archive coverage per month
against tgadsspy's own monthly Iran totals, channel/group catalog, sync health, collector
deliveries and Actions minutes. Yesterday's numbers are kept in data/daily_stats.json.

Run from sync.yml once a day (needs GH_TOKEN with issues:write, actions:read).
"""
import datetime as dt
import glob
import json
import os
import pathlib
import re
import subprocess

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATS_F = DATA / "daily_stats.json"
LABEL = "daily-report"
REPO = os.environ.get("GITHUB_REPOSITORY", "morteza13831383a-hue/nardbon-ads-archive")
OWNER = os.environ.get("GITHUB_REPOSITORY_OWNER", REPO.split("/")[0])
NOW = dt.datetime.now(dt.timezone.utc)


def jalali(d):
    """Gregorian date -> 'YYYY/MM/DD' in the Persian calendar."""
    gy, gm, gd = d.year, d.month, d.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    jm = 1 + days // 31 if days < 186 else 7 + (days - 186) // 30
    jd = 1 + (days % 31 if days < 186 else (days - 186) % 30)
    return f"{jy}/{jm:02d}/{jd:02d}"


FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))


def tehran(iso):
    """'2026-11-14T17:39:00Z' or with offset -> '1405/08/23 21:09' (Tehran time)."""
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(TEHRAN)
    except ValueError:
        return "—"
    return f"{jalali(t)} {t:%H:%M}"


def n(x):
    return f"{x:,}".replace(",", "٬").translate(FA) if isinstance(x, int) else str(x).translate(FA)


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def collect():
    ads = {}
    for f in glob.glob(str(DATA / "ads" / "*.json")):
        for a in json.load(open(f, encoding="utf-8")):
            ads[a["id"]] = a
    by_src = {}
    by_month = {}
    for a in ads.values():
        by_src[a.get("source") or "?"] = by_src.get(a.get("source") or "?", 0) + 1
        m = (a.get("first_seen") or "")[:7]
        by_month[m] = by_month.get(m, 0) + 1
    ch = json.loads((DATA / "channels.json").read_text("utf-8"))
    rows = [dict(zip(ch["fields"], r)) for r in ch["rows"]]
    state = json.loads((DATA / "state.json").read_text("utf-8"))
    return {
        "date": NOW.strftime("%Y-%m-%d"),
        "ads": len(ads), "ads_by_source": by_src, "ads_by_month": by_month,
        "channels": sum(1 for r in rows if (r.get("kd") or "channel") == "channel"),
        "groups": sum(1 for r in rows if r.get("kd") == "group"),
        "tgstat": sum(1 for r in rows if r.get("ts")),
        "region": {k: sum(1 for r in rows if r.get("rg") == k) for k in ("ir", "af")},
        "with_bio": sum(1 for r in rows if r.get("d")),
        "iran_bought": sum(1 for r in rows if r.get("ia")),
        "state": state,
    }


def site_month_totals(months):
    """tgadsspy's own count of Iran ads per month (public snapshot pages, one request each)."""
    out = {}
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (compatible; nardbon-archive/1.0)"
    for m in months:
        try:
            r = s.get(f"https://tgadsspy.com/countries/ir/snapshot/{m}", timeout=30)
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", " ", r.text, flags=re.S))) if r.ok else ""
            hit = re.search(r"([\d,]+)\s+Telegram ad creatives targeting Iran during", text)
            if hit:
                out[m] = int(hit[1].replace(",", ""))
        except requests.RequestException:
            pass
    return out


def deliveries():
    since = (NOW - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {}
    for label, path in (("آرشیو تبلیغات (کرال صفحه‌ها)", "data/html-archive-import"),
                        ("کانال‌های tgadsspy", "data/channel-html-import"),
                        ("TGStat", "data/tgstat-import")):
        lines = [l for l in sh("git", "log", f"--since={since}", "--format=%cI", "--", path).splitlines() if l]
        last = sh("git", "log", "-1", "--format=%cI", "--", path).strip()
        out[label] = (len(lines), last)
    return out


def minutes():
    """Billed minutes (rounded up per run) for the last 24 h and since the 1st of the month."""
    month_start = NOW.replace(day=1, hour=0, minute=0, second=0).strftime("%Y-%m-%d")
    raw = sh("gh", "api", "--paginate", f"repos/{REPO}/actions/runs?per_page=100&created=>={month_start}",
             "--jq", ".workflow_runs[] | [.run_started_at, .updated_at] | @tsv")
    day, month = 0, 0
    for line in raw.splitlines():
        try:
            a, b = line.split("\t")
            start = dt.datetime.fromisoformat(a.replace("Z", "+00:00"))
            secs = (dt.datetime.fromisoformat(b.replace("Z", "+00:00")) - start).total_seconds()
        except ValueError:
            continue
        billed = max(1, -(-int(secs) // 60))
        month += billed
        if start >= NOW - dt.timedelta(days=1):
            day += billed
    days_in_month = ((NOW.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)).day
    projected = month + round(day * (days_in_month - NOW.day + 1 - NOW.hour / 24))   # rest of month at today's rate
    return day, month, projected


def build(cur, prev):
    d = lambda k: cur[k] - prev.get(k, cur[k]) if prev else 0
    st = cur["state"]
    runs = [r for r in st.get("runs", []) if r.get("at", "") >= (NOW - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")]
    bad = [r for r in runs if not r.get("ok")]
    sw = st.get("adv_sweep") or {}
    months = sorted(m for m in cur["ads_by_month"] if re.fullmatch(r"\d{4}-\d{2}", m))
    site = site_month_totals(months)
    total_site = sum(site.values())
    lines = [f"@{OWNER} گزارش روزانه آرشیو نردبون — {tehran(NOW.isoformat())} (به وقت تهران)", ""]
    lines += ["## خلاصه", "",
              f"- **تبلیغ‌ها:** {n(cur['ads'])} (+{n(d('ads'))} در ۲۴ ساعت)"
              + (f" — {n(round(100 * cur['ads'] / total_site, 1))}٪ از {n(total_site)} تبلیغ ایرانی سایت" if total_site else ""),
              f"- **کانال‌ها:** {n(cur['channels'])} (+{n(d('channels'))}) · **گروه‌ها:** {n(cur['groups'])} (+{n(d('groups'))})",
              f"- **همگام‌سازی:** {n(len(runs))} اجرا در ۲۴ ساعت، {n(len(bad))} ناموفق"
              + (f" · آخرین ارسال موفق به هاست: {tehran(st['last_upload_ok'])}" if st.get("last_upload_ok") else ""),
              ""]
    lines += ["## آرشیو تبلیغات", "", "| منبع | تعداد | ۲۴ ساعت |", "|---|---:|---:|"]
    names = {"live": "تبلیغ‌های جدید (API)", "advertiser": "تاریخچه تبلیغ‌دهنده‌ها (API)",
             "html-archive": "کرال صفحه‌ها", "backfill": "تبلیغ‌های ماندگار (proven)"}
    for k, v in sorted(cur["ads_by_source"].items(), key=lambda kv: -kv[1]):
        pv = (prev.get("ads_by_source") or {}).get(k, v) if prev else v
        lines.append(f"| {names.get(k, k)} | {n(v)} | +{n(v - pv)} |")
    lines += ["", f"تاریخچه تبلیغ‌دهنده‌ها: {n(len(sw.get('done', {})))} از {n(sw.get('known', 0))} تبلیغ‌دهنده بررسی شده.", ""]
    if site:
        lines += ["| ماه | سایت | آرشیو ما | پوشش |", "|---|---:|---:|---:|"]
        for m in months:
            if m in site:
                ours = cur["ads_by_month"].get(m, 0)
                lines.append(f"| {m} | {n(site[m])} | {n(ours)} | {n(round(100 * ours / site[m], 1))}٪ |")
        lines.append("")
    lines += ["## کانال‌ها و گروه‌ها", "",
              f"- از TGStat: {n(cur['tgstat'])} (+{n(d('tgstat'))})",
              f"- ایران: {n(cur['region']['ir'])} · افغانستان: {n(cur['region']['af'])} · بقیه نامشخص",
              f"- دارای بیو: {n(cur['with_bio'])} · با خرید تبلیغ‌دهنده ایرانی: {n(cur['iran_bought'])}", ""]
    lines += ["## کرالرها (بسته‌های رسیده در ۲۴ ساعت)", ""]
    stale = []
    for label, (count, last) in deliveries().items():
        when = tehran(last) if last else "—"
        lines.append(f"- {label}: {n(count)} بسته · آخرین: {when}")
        if not last or dt.datetime.fromisoformat(last) < NOW - dt.timedelta(hours=12):
            stale.append(label)
    lines.append("")
    day, month, projected = minutes()
    lines += ["## دقیقه‌های GitHub Actions", "",
              f"- ۲۴ ساعت گذشته: {n(day)} · این ماه تا الان: {n(month)} · برآورد کل ماه با مصرف امروز: {n(projected)} از ۲٬۰۰۰ رایگان", ""]
    warn = []
    if bad:
        warn.append(f"{n(len(bad))} اجرای ناموفق: " + "; ".join((r.get('error') or '')[:80] for r in bad[:3]))
    if stale:
        warn.append("بیش از ۱۲ ساعت بسته جدیدی نرسیده: " + "، ".join(stale) + " — ابزار ChatGPT را بررسی کنید.")
    if projected > 2000:
        warn.append("برآورد دقیقه‌های این ماه از سهمیه رایگان بیشتر است.")
    if (st.get("api_blocked_until") or "") > NOW.strftime("%Y-%m-%dT%H:%M:%SZ"):
        warn.append(f"API تبلیغات تا {st['api_blocked_until']} متوقف است (محدودیت سایت).")
    lines += ["## هشدارها", ""] + ([f"- ⚠️ {w}" for w in warn] or ["- موردی نیست."])
    return "\n".join(lines), bool(warn)


HISTORY_F = DATA / "report_history.json"
TARGETS = {"ads": None, "channels": 547000, "groups": 4500}   # ads target = tgadsspy's own Iran total


def short_report():
    """Four lines, posted hourly as a comment on today's issue (one e-mail each, nothing else)."""
    cur = collect()
    hist = json.loads(HISTORY_F.read_text("utf-8")) if HISTORY_F.exists() else {"points": [], "site_total": None, "site_day": ""}
    today = NOW.astimezone(TEHRAN).strftime("%Y-%m-%d")
    if hist.get("site_day") != today or not hist.get("site_total"):        # site total once a day
        months = sorted(m for m in cur["ads_by_month"] if re.fullmatch(r"\d{4}-\d{2}", m))
        total = sum(site_month_totals(months).values())
        if total:
            hist["site_total"], hist["site_day"] = total, today
    point = {"t": NOW.isoformat(), "ads": cur["ads"], "channels": cur["channels"], "groups": cur["groups"]}
    pts = [p for p in hist["points"] if p["t"] >= (NOW - dt.timedelta(days=2)).isoformat()]
    prev = pts[-1] if pts else None
    pts.append(point)
    hist["points"] = pts
    HISTORY_F.write_text(json.dumps(hist, ensure_ascii=False), "utf-8")

    # rate from the last ~3 h, projected to midnight Tehran
    base = next((p for p in pts if p["t"] >= (NOW - dt.timedelta(hours=3)).isoformat()), pts[0])
    hours = max((NOW - dt.datetime.fromisoformat(base["t"])).total_seconds() / 3600, 0.5)
    local = NOW.astimezone(TEHRAN)
    left_today = (24 - local.hour - local.minute / 60)
    rate = {k: (point[k] - base[k]) / hours for k in ("ads", "channels", "groups")}
    by_night = {k: max(0, round(rate[k] * left_today)) for k in rate}
    new = {k: point[k] - prev[k] if prev else 0 for k in ("ads", "channels", "groups")}

    problems = []
    st = cur["state"]
    last_hour = [r for r in st.get("runs", []) if r.get("at", "") >= (NOW - dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")]
    failed = [r for r in last_hour if not r.get("ok")]
    if failed:
        problems.append("همگام‌سازی خطا داد")
    if (st.get("api_blocked_until") or "") > NOW.strftime("%Y-%m-%dT%H:%M:%SZ"):
        problems.append("API تبلیغات موقتاً متوقف است")
    for label, (_, last) in deliveries().items():
        if not last or dt.datetime.fromisoformat(last) < NOW - dt.timedelta(hours=6):
            problems.append(f"{label} بیش از ۶ ساعت چیزی نفرستاده")

    site = hist.get("site_total")
    ads_left = f" · {n(site - cur['ads'])} مانده ({n(round(100 * cur['ads'] / site))}٪ کامل)" if site else ""
    ch_left = f" · {n(round(100 * cur['channels'] / TARGETS['channels']))}٪ از حدود ۵۴۷ هزار"
    lines = [
        f"**{tehran(NOW.isoformat())}** — " + ("✅ همه‌چیز عادی است" if not problems else "⚠️ " + "، ".join(problems)),
        f"- تبلیغ: **+{n(new['ads'])}** · کل {n(cur['ads'])}{ads_left}",
        f"- کانال: **+{n(new['channels'])}** · کل {n(cur['channels'])}{ch_left} · گروه: +{n(new['groups'])} (کل {n(cur['groups'])})",
        f"- تا آخر امشب حدوداً: +{n(by_night['ads'])} تبلیغ، +{n(by_night['channels'])} کانال",
    ]
    text = "\n".join(lines)

    title = f"گزارش نردبون {jalali(local)}"
    sh("gh", "label", "create", LABEL, "--repo", REPO, "--color", "0E8A16", "--description", "Status report")
    issues = json.loads(sh("gh", "api", f"repos/{REPO}/issues?labels={LABEL}&state=open&per_page=20") or "[]")
    todays = [i for i in issues if i.get("title") == title]
    for i in issues:
        if i.get("title") != title:
            sh("gh", "api", "-X", "PATCH", f"repos/{REPO}/issues/{i['number']}", "-f", "state=closed")
    if todays:
        sh("gh", "api", f"repos/{REPO}/issues/{todays[0]['number']}/comments", "-f", f"body={text}")
    else:
        body = f"@{OWNER} گزارش‌های امروز در این صفحه می‌آیند (هر ساعت یک خط کوتاه).\n\n{text}"
        sh("gh", "api", f"repos/{REPO}/issues", "-f", f"title={title}", "-f", f"body={body}", "-f", f"labels[]={LABEL}")
    print(text)


def main():
    import sys
    if "--hourly" in sys.argv:
        return short_report()
    cur = collect()
    prev = json.loads(STATS_F.read_text("utf-8")) if STATS_F.exists() else {}
    body, has_warn = build(cur, prev)
    title = f"گزارش روزانه {jalali(NOW)}" + (" — ⚠️ نیاز به توجه" if has_warn else "")
    sh("gh", "label", "create", LABEL, "--repo", REPO, "--color", "0E8A16", "--description", "Daily status report")
    for num in sh("gh", "issue", "list", "--repo", REPO, "--label", LABEL, "--state", "open", "--json", "number",
                  "--jq", ".[].number").split():
        sh("gh", "issue", "close", num, "--repo", REPO)
    out = sh("gh", "issue", "create", "--repo", REPO, "--title", title, "--label", LABEL, "--body", body)
    print(out.strip() or body)
    keep = {k: v for k, v in cur.items() if k != "state"}
    STATS_F.write_text(json.dumps(keep, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
