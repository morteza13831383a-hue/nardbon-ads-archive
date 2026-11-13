# دریافت یک‌بارهٔ آرشیو تبلیغات ایران

این ابزار فقط صفحات عمومی HTML و sitemap سایت `tgadsspy.com` را می‌خواند؛ هیچ endpoint از API منبع فراخوانی نمی‌شود. کرالر کانال‌ها در این مسیر اجرا نمی‌شود.

## رفتار

- در شروع، زمان مرزی ثبت می‌شود. فقط تبلیغ‌های دارای کشور `IR` و تاریخ اولین مشاهده پیش از آن زمان نگهداری می‌شوند. متن فارسی به‌تنهایی معیار ایران نیست.
- ابتدا صفحات ایران و تبلیغ‌دهنده‌های مرتبط بررسی می‌شوند؛ سپس sitemap برای یافتن تبلیغ‌های قدیمی‌تر پیمایش می‌شود.
- صفحهٔ پردازش‌شده دوباره دریافت نمی‌شود. SQLite صف، رکوردها و خطاها را نگه می‌دارد. این استخراج دوره‌ای نیست؛ اجراهای بعدی فقط همان کار ناتمام را ادامه می‌دهند.
- درخواست‌ها ترتیبی‌اند، فاصلهٔ شروع درخواست‌ها دست‌کم چهار ثانیه و با مکث متغیر تا شش ثانیه است و robots.txt، پاسخ 429 و توقف دسترسی رعایت می‌شود.
- اطلاعات JSON-LD، مقصد، تاریخ‌ها، متن، رسانه، آمار، نمودار روزانه، فهرست نمایش‌داده‌شدهٔ کانال‌های میزبان، لینک‌ها و بخش‌های متنی صفحه حفظ می‌شوند. عکس‌ها با مسیر پردازش WebP فعلی ارسال می‌شوند. ویدئوها طبق رفتار موجود فقط لینک دارند.

## اجرای GitHub Actions

Workflow با نام **One-off Iran HTML archive** اضافه شده است؛ زمان‌بندی ندارد و خودش اجراهای بعدی را ایجاد نمی‌کند. در تست واقعی، GitHub Actions فهرست ایران را خواند اما اولین صفحهٔ جزئیات پاسخ ۴۰۳ داد. همان صفحه در محیط Cloud قابل دریافت بود. بنابراین حالت پیش‌فرض GitHub، دریافت دوبارهٔ صفحات نیست: خروجی‌های جمع‌آوری‌شده در محیط دارای دسترسی عادی را وارد و ارسال می‌کند.

فایل‌های `ads-*.jsonl.gz` استخراج‌شده را در `data/html-archive-import/` قرار دهید و commit کنید؛ همین تغییر، اجرای import/upload را آغاز می‌کند. صف Cloud از checkpoint ذخیره‌شده ادامه پیدا می‌کند و رکوردهای واردشده نیز در checkpoint GitHub پردازش‌شده علامت می‌خورند. هیچ توکن آپلودی به محیط جمع‌آوری منتقل نمی‌شود.

برای ادامه: Actions ← One-off Iran HTML archive ← Run workflow.

- `import_only`: پیش‌فرض روشن؛ فقط ورود و ارسال فایل‌های قبلاً جمع‌آوری‌شده، بدون تماس با منبع. فقط پس از برطرف‌شدن دسترسی ۴۰۳ GitHub آن را خاموش کنید.
- `upload_exports`: فعلاً خاموش؛ گیرندهٔ فعلی در تست واقعی CSV را با `400 type not allowed` رد کرد. تا اصلاح allowlist، فایل‌های پنل و عکس‌ها ارسال و جزئیات کامل در مخزن/artifact حفظ می‌شوند. برای ارسال CSV/gzip پس از اصلاح گیرنده روشن شود.
- `max_pages`: سقف درخواست در این نوبت، پیش‌فرض ۱۵۰۰. زمان کرال نیز حداکثر ۹۰ دقیقه است؛ رسیدن به سقف، صف را تکمیل‌شده علامت نمی‌زند.
- `resume_run_id`: خالی بماند تا آخرین checkpoint سالم شاخهٔ main بازیابی شود؛ یا شناسهٔ اجرای مشخص را وارد کنید.
- `retry_failed`: فقط پس از رفع علت خطا فعال شود.

Checkpoint و عکس‌های هنوز ارسال‌نشده در artifact به نام `iran-html-checkpoint` ذخیره می‌شوند و خروجی کامل هر نوبت در `iran-html-exports` است. مهلت نگهداری artifact برابر ۹۰ روز است؛ یک نسخهٔ checkpoint را پیش از انقضا دانلود و نگه دارید. از دست‌رفتن checkpoint با وجود گزارش قبلی، اجرای تازه را متوقف می‌کند تا استخراج از صفر تکرار نشود.

قفل concurrency با workflow فعلی مشترک است تا فایل‌های پنل هم‌زمان بازنویسی نشوند. **هنگام اجرای طولانی آرشیو، sync عادی منتظر می‌ماند.** طول نوبت‌ها را متناسب با نیاز به آپدیت زنده انتخاب کنید.

Secrets مورد استفاده فقط `UPLOAD_URL` و `UPLOAD_TOKEN` هستند. دسترسی کلید API منبع به این workflow داده نمی‌شود. وضعیت قدیمی sync و ساختار فایل‌های ماهانه حفظ و رکوردهای جدید با شناسهٔ موجود ادغام می‌شوند؛ دادهٔ جدیدتر API به عقب برگردانده نمی‌شود.

## خروجی و ارسال

- فایل‌های پنل: همان `tgads/data/YYYY-MM.json`؛ JSON معتبر شامل آرایه، هر رکورد در یک خط.
- عکس‌های آماده‌شده: همان `tgads/media/<id>.webp`، عرض حداکثر ۹۰۰ پیکسل.
- جزئیات اضافه: `tgads/exports/archive-html/ads-00001.jsonl.gz` و `ads-00001.csv` و قسمت‌های بعدی.
- راهنما و پوشش: `tgads/exports/archive-html/manifest.json`، شامل تعداد، زمان مرزی، خطاها، حجم و SHA-256. فقط فایل‌های فهرست‌شده در manifest متعلق به آخرین خروجی‌اند؛ قسمت‌های قدیمی اضافی ممکن است هنوز روی هاست باشند.
- CSV با UTF-8 BOM و محافظت از فرمول‌های Excel ساخته می‌شود. بخش‌ها با بودجهٔ حدود چهار مگابایت متن خام تولید می‌شوند؛ اگر یک رکورد به‌تنهایی بزرگ‌تر باشد، حجم واقعی در manifest مشخص است. محدودیت آپلود و پسوندهای مجاز گیرنده باید این فایل‌ها را بپذیرند؛ خطای ارسال به‌عنوان موفقیت ثبت نمی‌شود.

در هر نوبت حداکثر ۱۰۰ عکس آماده می‌شود. متن استخراج‌شده با رسانهٔ کامل و ارسال کامل یکی نیست؛ فایل‌های pending در اجراهای ادامه پردازش می‌شوند.

## محدودیت پوشش

صفحهٔ ایران ۵۰ تبلیغ و صفحهٔ روزانه ۱۰۰ تبلیغ نشان می‌دهد. فیلتر `geo=IR` روی آرشیو روزانه در بررسی ما اعمال نشد. sitemap نیز شناسه‌های همهٔ کشورها را دارد و کشور را مشخص نمی‌کند؛ بنابراین تکمیل مسیر عمومی می‌تواند نیازمند بررسی تعداد بسیار زیادی صفحهٔ غیرایرانی باشد و زمان/دقیقهٔ Actions زیادی مصرف کند.

خالی‌شدن صف فقط یعنی URLهای کشف‌شده پردازش شده‌اند، نه اینکه تمام دیتابیس منبع قابل دریافت بوده است. تعداد اعلامی منبع با تعداد استخراج‌شده در گزارش جداست. `complete_iran_archive_verified` تا تطبیق مستقل پوشش `false` می‌ماند؛ صفحات حذف‌شده، مخفی، مسدود یا اصلاً معرفی‌نشده قابل تضمین نیستند. فهرست میزبان‌های هر تبلیغ در منبع فقط **Top channels** است، نه تمام میزبان‌ها. آمار و آخرین مشاهده در زمان دریافت صفحه خوانده می‌شوند؛ زمان مرزی فقط انتخاب تبلیغ‌های قدیمی را محدود می‌کند.

## اجرای محلی

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/archive_html.py --max-pages 80
.venv/bin/python scripts/archive_publish.py             # فقط ادغام محلی
.venv/bin/python scripts/archive_publish.py --upload --panel-only  # گیرندهٔ فعلی
.venv/bin/python scripts/archive_publish.py --upload               # پس از اجازهٔ CSV/gzip
```

برای ورود داده‌های محیط دیگر و ساخت خروجی بدون تماس با منبع:

```bash
.venv/bin/python scripts/archive_html.py --import-dir data/html-archive-import --export-only
```

برای ساخت دوبارهٔ خروجی بدون هیچ دریافت:

```bash
.venv/bin/python scripts/archive_html.py --export-only
```

اجرای تست‌ها:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## ادامهٔ خودکار همان استخراج یک‌باره روی محیط دارای دسترسی

روی محیط Cloud/VPS که صفحات عمومی برایش قابل دریافت است و `gh` از قبل برای همین مخزن احراز هویت شده:

```bash
.venv/bin/python scripts/archive_collect.py \
  --repo morteza13831383a-hue/nardbon-ads-archive \
  --work-dir /path/to/persistent/archive-work \
  --pages-per-slice 1000 --max-slices 0
```

این فرمان زمان‌بندی یا آپدیت دوره‌ای نمی‌سازد. فقط همان زمان مرزی ثابت و صف باقی‌مانده را تا اتمام URLهای کشف‌شده دنبال می‌کند، و هر نوبت فقط رکوردهای جدید را در قطعه‌های تغییرناپذیر به GitHub می‌رساند. توکن GitHub فقط از احراز هویت موجود `gh` خوانده می‌شود؛ توکن هاست در محیط جمع‌آوری لازم نیست. در اولین خطای دسترسی یا از دست‌رفتن اتصال/اعتبار GitHub متوقف می‌شود و checkpoint را نگه می‌دارد. کامل‌بودن آرشیو حتی پس از خالی‌شدن صف نیاز به بررسی پوشش دارد. اجرای چندروزه به محیط و دیسک پایدار و اعتبار اتصال GitHub نیاز دارد؛ این چت تضمین میزبانی دائمی نیست.

در پاسخ ۴۲۹، دریافت از منبع بلافاصله متوقف می‌شود؛ تلاش مجدد فوری انجام نمی‌شود. پاسخ‌های ۴۰۱ و ۴۰۳ نیز کل کار را متوقف می‌کنند. ادامه پس از چنین خطایی باید پس از بررسی علت و رعایت Retry-After انجام شود؛ ابزار IP یا حساب را برای دورزدن محدودیت عوض نمی‌کند.


## بهینه‌سازی کشف تبلیغات ایران

صف عمومی sitemap اولویت ۳۰ دارد. تبلیغاتِ صریحاً ایرانی در فهرست‌ها اولویت ۱ دارند؛ تبلیغات دیگر همان تبلیغ‌دهنده از روی بخش `More from this advertiser` و لینک‌های ذخیره‌شده اولویت ۲ دارند؛ صفحهٔ همان تبلیغ‌دهنده اولویت ۳ دارد. پیشنهادهای تصادفیِ هم‌دسته برای این اولویت استفاده نمی‌شوند. کشور هر تبلیغ همچنان مستقل از این نشانه‌ها در صفحهٔ خودش تأیید می‌شود.

لینک‌های قبلاً ذخیره‌شده بدون دریافت دوبارهٔ HTML برای ساخت این گراف استفاده شدند. جدول `discovery_edges` رابطهٔ والد/فرزند و دلیل کشف را نگه می‌دارد. صف عمومی حذف نشده و فقط پس از اتمام نامزدهای مرتبط بررسی می‌شود. گزارش `last_slice_performance` تعداد صفحات بررسی‌شده، تبلیغات ایرانی جدید و زمان هر نوبت را برای سنجش بازده و برآورد زمان نگه می‌دارد. پوشش کاملِ گرافِ مرتبط به‌تنهایی تضمین نیست؛ تبلیغات جدا از این گراف ممکن است فقط از مسیرهای عمومی دیگر کشف شوند.

## Yield plan (branch `crawler-yield`, `scripts/archive_yield.py`)

Goal: find the ~56k Iran ads without opening ~750k pages from all countries, with the same
request rate, no source API and no blocked paths.

| Step | Public path | What it gives |
|---|---|---|
| 1 | `/countries/ir/snapshot/YYYY-MM` (2026-01 .. cutoff month) | exact Iran total per month and per niche, e.g. "16,687 ... targeting Iran during September 2026", "VPN 2,331". A 404 for the month before the first snapshot proves where Iran coverage starts. |
| 2 | sitemap ids, dated offline | cuid v1 ids carry the creation time (`int(id[1:9], 36)` ms); measured on 4,611 ads it is within -2 h and +71.3 h of first_seen (median 0 h). Ids more than 7 days after the cutoff, or two+ months before Iran coverage, are skipped without a request (closer ones only go to the back); the rest are ordered by (site total − extracted) ÷ remaining ids of their month. |
| 3 | `/channels/<username>/advertisers` for channels listed under "Where this ad ran" on Iran ads | advertisers outside the current graph who bought in the same (Persian) channels |
| 4 | `/advertisers/<slug>/niches/<niche>` from advertiser pages | more of one advertiser's ads than its capped main page |

Every queue row records `via` (strategy) and `month`; `report()["coverage"]` gives, per month,
site total vs extracted and, per strategy, Iran ads per 100 requests.

### Test before switching over (same pace, ~1 h each)
1. Run one slice of 200 pages with this branch on a copy of `checkpoint.sqlite3`.
2. Check `coverage.by_month` (are all snapshot totals stored? does the 404 month exist?).
3. Check `coverage.by_strategy`: `channel_advertisers` / `advertiser_via_channel` /
   `advertiser_niche` vs the plain sitemap (`original`). Keep a strategy only if its
   Iran-per-100 beats the sitemap's.
4. Check the `Yield plan` log line: `after_cutoff_by_id`, `before_iran_by_id` = pages saved.

### Completeness
Done when, for every month, extracted ≥ site total (small drift allowed because the site keeps
counting until the cutoff), the sum of months matches the `/countries/ir` total, and the
per-niche counts of each month match. An empty queue alone is not proof.
