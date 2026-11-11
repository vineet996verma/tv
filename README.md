# Clean IPTV playlist

Takes the free iptv-org list (11,000+ channels, many dead) and keeps only:

- Indian channels: **Hindi** first, then **Regional**, each split into News, Movies, Entertainment, Music, Kids and so on.
- **English News**, **English Movies**, **English Entertainment**, **Kids**, **Documentary & Science**.

Every stream is tested and dead ones are dropped. A **programme guide (EPG)** is built for the channels that are left, from open-epg.com (Indian channels) and i.mjh.nz (Pluto, Samsung TV Plus, Plex, Roku).

The first test run (11 Nov 2026) kept **1,723 working channels** out of 2,615. **897** of them have programme info.

## Hosting on GitHub (free, no home device needed)

GitHub rebuilds the list every morning at 6 AM India time and serves it at a fixed address.

1. Sign in at github.com (free account).
2. Make a new **public** repository, e.g. `tv`.
3. Upload `build.py`, `README.md`, `.gitignore` and the `.github` folder. In the web upload, drag the whole folder in so `.github/workflows/build.yml` keeps its path.
4. Repository **Settings → Pages → Source: GitHub Actions**.
5. **Actions** tab → "Build clean playlist" → **Run workflow**. The first run takes about 5 minutes.

Your addresses (replace `USERNAME` and `tv`):

- Playlist: `https://USERNAME.github.io/tv/playlist.m3u`
- Guide: `https://USERNAME.github.io/tv/guide.xml` (a zipped copy, `guide.xml.gz`, is also there)
- Report: `https://USERNAME.github.io/tv/report.txt`

## On the TV

**IPTV Smarters Pro** (has favourites):
Add user → *Load your playlist or file/URL* → paste the playlist address. Then go to Settings → EPG (or "Install EPG") and add the guide address if the guide doesn't appear on its own.

**TiviMate**:
Remove the old iptv-org playlist and add the new playlist address. TiviMate reads the guide address from the playlist by itself. If it doesn't, go to Settings → EPG → Add source → paste the guide address.

## Notes

- GitHub's servers are in the US. Some Indian streams only work from India, and they answer "403 blocked" when tested from there. Those are **kept**, so a few of them may still be dead.
- Running `python build.py --drop-blocked` on a PC in India gives the strictest list.
- These are free public streams, so channels come and go. The nightly rebuild keeps the list fresh.
