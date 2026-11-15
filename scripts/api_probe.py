"""Manual diagnostic: are Persian channels from our catalog Iranian advertisers themselves?"""
import os, time, requests
API = "https://tgadsspy.com/api/v1"
S = requests.Session(); S.headers["X-Api-Key"] = os.environ["TGADS_API_KEY"]
slugs = """tg-akbarfathi56_telegram
tg-nasle2500pasargd
tg-pdfaddachannel
tg-jsdhbsjjhxsjjs
tg-awknpers
tg-toeflieltsapplyabroad
tg-dirancast_official
tg-i666ggg
tg-cryptotrade_free
tg-tahlil_abii
tg-vamdhifore159
tg-bours_pishroo
tg-tse_derivatives
tg-mohallell
tg-mrdollarii
tg-mirjalilifx
tg-arzdigitalls
tg-baft_ba_sayeh
tg-tiredstudent
tg-eghtesad124
tg-karyabijavanrood19
tg-thefeling
tg-skr_official
tg-gfytreiuyewooopkjh
tg-master_abedi
tg-faalhoosh
tg-iribnewsint
tg-vandadiha
tg-only_r20
tg-ali_amraee
tg-freeeqa
tg-sedgraphy
tg-english_t9me
tg-akhbar_pezeshki_weekly
tg-kanoon_atu
tg-rzawaltone
tg-amozeshborsi
tg-amoozeshigahreman
tg-utcounseling
tg-airdrop_arzdigital_channel""".split()
hit = ads = 0
for slug in slugs:
    r = S.get(f"{API}/ads", params={"advertiser": slug, "geo": "IR", "limit": 100}, timeout=40)
    if r.status_code == 429:
        print("429, stopping"); break
    t = (r.json().get("meta") or {}).get("total") or 0 if r.ok else 0
    hit += t > 0; ads += t
    print(slug, r.status_code, t)
    time.sleep(5)
print(f"SUMMARY channels={len(slugs)} advertisers={hit} iran_ads={ads}")
