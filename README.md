# Pappa

## What you need

- Python 3.10 or newer (`python --version` to check)
- An internet connection while it runs
- The market is NIFTY options; times are IST

## Get the code (do once)

1. `git clone https://github.com/poojitha376/Pappa_Project.git`
2. `cd Pappa_Project`

## Setup (do once)

1. Open a terminal in this folder.
2. Run: `pip install -r requirements.txt`
   - On Windows, if `pip` isn't recognized as a command, use `python -m pip install -r requirements.txt` instead — same effect, just calls pip through Python directly.

## Run (every trading day)

1. Start it **before 09:18 IST** so you don't miss early rows.
2. Run: `python run.py`
3. Leave this terminal open the whole day. Closing it stops collection.
4. Keep the laptop awake and online (Windows: Settings → Power → Sleep → Never).
5. When done for the day, press `Ctrl+C` in the terminal.

## Open the dashboard

1. In the terminal, run: `hostname -I`
2. Copy the first number it prints (looks like `172.24.29.186`).
3. In your browser, open: `http://` + that number + `:8000`
   Example: `http://172.24.29.186:8000`
4. If `http://localhost:8000` opens for you, you can just use that.
5. The IP can change after a computer restart — if the page stops opening, run `hostname -I` again and use the new number.

## Enter the strikes

1. On the page, type the CE strike in the first box and the PE strike in the second box (numbers only).
2. Click **Save strikes**.
3. Rows fill in on their own at each scheduled time (09:18, 09:28, 09:43, then every 15 min to 15:40).
4. To change strikes later, type new ones and click **Save strikes** again.

## See past days

Use the **Day** dropdown at the top of the page.

## Spreadsheet

Every time a row updates on the dashboard, it's also written straight into
**`Pappa.xlsx`** in this folder — same values, same cell colours, one block per day with
a blank row gap between days. Open it any time (close it before market hours if your
spreadsheet program locks the file).

### Share it live with someone else (e.g. OneDrive)

By default the spreadsheet only lives on this computer. To make it show up for someone
else automatically:

1. Pick a folder that's synced to OneDrive or Google Drive, e.g.
   `C:\Users\<you>\OneDrive\Pappa.xlsx`.
2. Share that file (or its folder) from OneDrive/Drive with the other person's account —
   they can then open it in a browser (Excel Online / Google Drive preview) or their own
   synced folder, and it updates within seconds of you saving.
3. In `config.json`, set `"xlsx_path"` to that path. From this WSL setup, a Windows path
   like `C:\Users\pooji\OneDrive\Pappa.xlsx` is written as
   `/mnt/c/Users/pooji/OneDrive/Pappa.xlsx`. Example:
   ```json
   "xlsx_path": "/mnt/c/Users/pooji/OneDrive/Pappa.xlsx"
   ```
4. Restart `run.py`. Leave `"xlsx_path": null` to keep using the plain local file instead.

## Start a fresh day / clear everything

Stop it (`Ctrl+C`), then delete the data file:

```
rm -f data.db data.db-wal data.db-shm
```

Then run `python run.py` again. (Past days are lost when you do this.)

## Get the data as a file

In the terminal (in this folder):

```
sqlite3 -header -csv data.db "SELECT * FROM samples ORDER BY row_index;" > data.csv
```

Opens in Excel.

## If something looks wrong

- Status bar should say **feed live**. If it says **feed stale**, it lost the connection — it reconnects on its own; wait a minute.
- Rows for times before you started `run.py` show **✕** (missed). That is normal; there is no way to fill them in.
- `ModuleNotFoundError: No module named 'tzdata'` (Windows only) — Windows doesn't ship a timezone database the way Linux/Mac do, so Python needs the separate `tzdata` package. Run `python -m pip install tzdata` once, then try again. (`requirements.txt` installs this automatically on a fresh setup — this only bites if `tzdata` somehow got skipped.)
- More detail about how it works is in `docs/NOTES.md`.
