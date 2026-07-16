# AMZ FBM Cockpit — local dashboard

A local web front-end over the existing toolkit. It runs **only on your computer**, reads the
Helium 10 files you exported yourself, and **never connects to Amazon Seller Central**.

## Run it (once)
```
cd AMZ-FBM-Toolkit-v2_3_4-RC1
pip install -r requirements.txt        # includes flask
python dashboard/app.py
```
Open **http://127.0.0.1:5000** in your browser. Leave the terminal window open while you use it.
(Windows: use `python`. Mac/Linux: `python3` if needed.)

## How to use it
1. **Type a project name** (e.g. `nurse`) and your **seed phrase** in the top bar.
2. **Upload** your Helium 10 exports:
   - **Xray export** — the `.xlsx` you saved from Xray on the search page for your seed.
   - **Cerebro export** — the `.xlsx` of the 10 ASIN keyword data.
3. Click **▶ Run analysis**. The cockpit drives the real tools:
   - `research/asin_picker.py` → the 10 best ASIN
   - `research/phaseA_master.py` → ranked keywords (top 5 highlighted)
   - `research/seed_expand.py` → 5 expansion seeds
   - `pipeline.py` → the gate board
4. Build the listing — three ways, pick what fits:
   - **⌘ Build with Claude Code** — uses your **Pro/Max plan** via the local `claude` CLI. **No API
     key, no per-token cost** (shared subscription limits). Install Claude Code and sign in with your
     plan first (https://support.claude.com/en/articles/11145838); the button shows
     "Max plan · detected" when it's ready.
   - **⚡ Auto-build with API key** — paste your own Anthropic key in ⚙ Settings (in memory only).
   - **✨ Copy-paste brief** — get a ready-to-paste prompt, run it in claude.ai yourself, import the JSON.
   All three write the full title + 5 bullets + description + backend + **A+ 7 modules** +
   **10-photo brief** to the current Amazon rules (Jul-27-2026 title caps, 2026 image specs, A10).

   > We intentionally do NOT automate claude.ai in a browser — that violates Anthropic's terms and
   > can get your account suspended. Claude Code is the sanctioned way to use your subscription here.
5. Save Claude's result as `listing.json`, **Import** it (third upload box) → the page renders as a
   real **Amazon-style product page + A+ modules + 10-photo plan**.

## What it does NOT do
- No Seller Central connection, no publishing, no PPC changes, no credentials.
- The "Generate listing brief" button does not call any AI by itself — it produces a prompt you
  paste into Claude (your choice, your control, no API key).
- Photos are physical: the cockpit gives the exact photo brief; you/your designer shoot them.

## Notes
- Each project is a folder under `runs/<project>/` — the same folders the CLIs use. You can still
  run any CLI in a terminal; the dashboard just wraps them.
- If the ASIN panel says nothing and the run log shows **SHORT BATCH**, your Xray wasn't the exact
  search page for the seed — re-Xray that page and Run again.
