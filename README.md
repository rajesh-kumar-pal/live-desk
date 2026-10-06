# Patrika Live Desk – sitemap tracker

Every hour (06:15–23:15 IST), a GitHub Action reads the Google News sitemaps of Patrika and seven Hindi rivals: Amar Ujala, Aaj Tak, Zee Hindi, Bhaskar, ABP, NDTV Hindi and NBT. It keeps rival stories from the last 3 hours that fit Patrika's audience, marks each one **GAP** or **COVERED** against Patrika's last 12 hours, and commits the result to `data/`:

| File | What it holds |
|---|---|
| `data/wire.json` | Relevant rival stories, newest first. Each has its source, title, URL, IST time, audience tags (RAJ, MP, CG, CRIME, ACC, POL, WAR, CRK, MONEY, ENT), a score, and GAP/COVERED status with the closest Patrika match. |
| `data/patrika.json` | Patrika's own stories from the last 12 hours. |
| `data/tracker.json` | Health of each source for this run (ok, count, newest story, error note). |

Claude's scheduled tasks read these files from GitHub. The hourly sync adds editorial angles to the Live Desk page, and the 2-hourly run writes the coverage suggestions. None of this needs your Mac to be switched on.

## One-time setup (about 5 minutes)

1. On github.com, click **+ → New repository**. Name it `patrika-live-desk`, choose **Public**, and click **Create repository**.
   It has to be public so that Claude's scheduled runs can read the JSON without a password. It only ever holds public headlines and links.
2. On the new repo page, click **uploading an existing file**. Drag in `README.md` and the `scripts`, `tests` and `data` folders, then click **Commit changes**.
3. Add the workflow file. GitHub's uploader skips hidden folders, so create this one by hand:
   **Add file → Create new file**, name it `.github/workflows/tracker.yml`, paste in the contents of that file from this package, and click **Commit changes**.
4. Open **Actions → Sitemap tracker → Run workflow** to do a first run. After about a minute, `data/wire.json` will have stories in it.
   If the run fails, open it and check the "Read sitemaps" step: it prints which sources failed and why.

After that it runs by itself every hour. GitHub sometimes starts scheduled runs 5–15 minutes late.

## Tuning

- Keywords and weights: edit `RULES` in `scripts/tracker.py`.
- Sources: edit `SOURCES`. If a source's sitemap list is left empty, the script finds it in that site's `robots.txt`.
- Time windows: change the `WIRE_HOURS` and `PATRIKA_HOURS` environment variables in the workflow.
- Test: `python -m unittest discover tests`. The test uses sample sitemaps and needs no network.
