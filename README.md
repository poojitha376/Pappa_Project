# Pappa

## Setup (do once)

1. Open a terminal in this folder.
2. Run: `pip install -r requirements.txt`

## Run (every trading day)

1. Run: `python run.py`
2. Leave this terminal open the whole day. Closing it stops collection.
3. When done for the day, press `Ctrl+C` in the terminal.

## Open the dashboard

1. In the terminal, run: `hostname -I`
2. Copy the first number it prints (looks like `172.24.29.186`).
3. In your browser, open: `http://` + that number + `:8000`
   Example: `http://172.24.29.186:8000`
4. (If `http://localhost:8000` opens for you, you can just use that.)
5. The IP can change after a computer restart — if the page stops opening, run `hostname -I` again and use the new number.

## Enter the strikes

1. On the page, type the CE strike in the first box and the PE strike in the second box (numbers only).
2. Click **Save strikes**.
3. Rows fill in on their own at each scheduled time (09:18, 09:28, 09:43, then every 15 min to 15:40).

## See past days

Use the **Day** dropdown at the top of the page.

## Get the data as a file

In the terminal (in this folder):

```
sqlite3 -header -csv data.db "SELECT * FROM samples ORDER BY row_index;" > data.csv
```

Opens in Excel.
