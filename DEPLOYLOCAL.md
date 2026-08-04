# Running the microCT Lab on your own PC

A guide for setting this up on a new machine from scratch. Should take about
fifteen minutes, most of it waiting for downloads.

---

## What you are actually installing

Two halves, and it helps to know which is which:

**The server** runs on *your* PC. It reads your scans, runs segmentation, and
keeps a small database of everything. This is what you install here.

**The web interface** is just a page. You can either open the hosted one at
<https://microctweb.vercel.app>, or serve it from your own machine. Either way
it is only HTML and JavaScript: once loaded it talks to the server on **your**
PC, so **your data never leaves your machine**. Nothing is uploaded anywhere.

The quickest route — and the one this guide takes — is to install the server and
use the hosted page. There is nothing to build and nothing to configure.

---

## Before you start

**1. Python 3.10 or newer, 64-bit.** From <https://www.python.org/downloads/>.
During installation tick **"Add python.exe to PATH"** — it is easy to miss and
nothing below works without it. Check with:

```
python --version
```

**2. Git.** From <https://git-scm.com/downloads>.

**3. Access to the repository.** `snehakorlakunta/microct-tools` is **private**,
so ask Sneha to add your GitHub account as a collaborator first. Without that the
clone below fails with an authentication error rather than a clear "no access"
message.

> **No GitHub account, or would rather skip all this?** There is a self-contained
> `localserver` folder that needs no Git, no repository access, and no internet
> during install — it bundles every Python package it needs. Ask for that instead
> and follow the README inside it. The rest of this guide is for the Git route.

---

## Step 1 — Get the code

Pick any folder you like. These instructions use `C:\microct`.

```
mkdir C:\microct
cd C:\microct
git clone https://github.com/snehakorlakunta/microct-tools.git
cd microct-tools
```

## Step 2 — Create a virtual environment

This keeps the project's packages separate from the rest of your Python install.

```
python -m venv .venv
.venv\Scripts\activate
```

Your prompt should now start with `(.venv)`. On macOS or Linux the activate line
is `source .venv/bin/activate` instead.

## Step 3 — Install

```
pip install -e .
```

The `-e` is **required**, not a preference. The application locates its own
`scripts/` folder and `.env` file relative to where the source lives; a normal
install copies the code elsewhere and it can no longer find either.

This downloads about 100 MB, so it needs an internet connection and a minute or
two.

## Step 4 — Make folders for your data

```
mkdir data results models state
```

- **data** — your scans. One folder per scan, containing its reconstructed
  `*_rec*.bmp` slices and the `*_rec.log` file the scanner wrote.
- **results** — where segmentation output goes.
- **models** — trained nnU-Net model folders.
- **state** — the small database and thumbnails.

They do not have to live here. If your scans are already on another drive or a
share, point at them in the next step instead of copying anything.

## Step 5 — Configure

Copy the example configuration:

```
copy .env.example .env
```

Open `.env` in Notepad and set the four paths. Use the full path to the folders
you just made:

```ini
MICROCT_DATA_ROOT=C:\microct\microct-tools\data
MICROCT_RESULTS_ROOT=C:\microct\microct-tools\results
MICROCT_MODELS_ROOT=C:\microct\microct-tools\models
MICROCT_STATE_DIR=C:\microct\microct-tools\state

MICROCT_HOST=127.0.0.1
MICROCT_PORT=8000
MICROCT_DEFAULT_DEVICE=auto
```

Then find the commented line near the bottom and **uncomment it** (delete the
leading `#`):

```ini
MICROCT_ALLOWED_ORIGINS=https://microctweb.vercel.app
```

That one line is what lets the hosted page talk to your server. Your browser
blocks it otherwise. Leave `MICROCT_API_TOKEN` commented out — no password is
needed.

## Step 6 — Start it

Two windows, both left open. In the first:

```
.venv\Scripts\activate
microct-web
```

You should see:

```
microCT Segmentation Lab  ->  http://127.0.0.1:8000

  A remotely-hosted UI may connect to this server:
    https://microctweb.vercel.app

    No token required — open the page and it connects.
```

Open a **second** terminal for the worker:

```
cd C:\microct\microct-tools
.venv\Scripts\activate
microct-worker
```

The worker is what actually runs jobs. Without it anything you queue sits at
**queued** forever — the single most common "it's broken" report, and it is not
broken.

To stop either one, click its window and press `Ctrl+C`.

## Step 7 — Open the interface

Go to **<https://microctweb.vercel.app>**.

The header should show a green **Connected**. That is it — no login, no token,
nothing to paste.

If Chrome asks permission to access devices on your local network, allow it.
That prompt is the browser confirming the page may talk to your own machine.

---

## Loading your first scan

1. **Copy a scan** into your `data` folder — the whole folder of `*_rec*.bmp`
   slices plus its `*_rec.log`.
2. In the interface go to **Datasets → Ingest datasets**. It reads each scan's
   log for voxel size, dimensions, scanner and voltage, and makes a thumbnail.
3. To segment, you need a trained model: put its folder in `models`, then
   **Models → Register model** and paste the path.
4. **New run** → pick a model and dataset. The worker takes it from there.

If you have been given a database backup instead, you can skip steps 1–3 — see
*Loading an existing catalogue* below.

---

## Optional extras

The base install runs the interface, the catalogue and the viewer. Two things
need more, and both need internet:

**Segmentation** (needs an NVIDIA GPU to be practical):

```
pip install torch --index-url https://download.pytorch.org/whl/cu121
pip install -e ".[seg]"
```

**Morphometry** — bone length and socket measurement, CPU only:

```
pip install -e ".[morph]"
```

Then run `microct-measure-worker` in a third window so measurement jobs do not
queue behind GPU work.

Without these the interface still runs and shows everything already in the
catalogue; you just cannot start new jobs of that type.

---

## Loading an existing catalogue

If someone sends you a registry backup (`.db` or `.zip`), you get their whole
catalogue — datasets, runs, review notes — rather than starting empty.

```
python scripts\backup_registry.py --report-paths
```

Stop the server, put the file at `state\registry.db`, and **delete any
`registry.db-wal` and `registry.db-shm`** sitting beside it. Those belong to the
previous database and would otherwise be replayed over what you just restored.

One catch: the catalogue stores **absolute paths** to the original machine's
scans. If your folders differ, ask them to rebuild the backup retargeted to you:

```
python scripts\backup_registry.py --out for-you.db --rewrite "C:\their\path=C:\your\path"
```

Never just copy `registry.db` out of a running install. The database uses
write-ahead logging, so recent work can still be sitting in the `-wal` file; a
plain copy opens fine and looks healthy while silently missing the newest
records. Always use `backup_registry.py`, which handles this.

---

## Running the interface locally instead of using the hosted page

Use this if you are on **Safari** (which refuses to let a hosted HTTPS page reach
your own machine), if you have no internet, or if you would simply rather not
depend on a hosted site.

The server already includes a working built-in interface — just open
<http://127.0.0.1:8000> instead of the Vercel address. You can skip
`MICROCT_ALLOWED_ORIGINS` entirely in that case.

For the newer interface locally you need the frontend repository as well
(`snehakorlakunta/microctweb`, also private), Node.js 20+, and:

```
git clone https://github.com/snehakorlakunta/microctweb.git
cd microctweb
npm install
npm run build
```

Then add this to the server's `.env` and restart it:

```ini
MICROCT_WEB_DIR=C:\microct\microctweb\out
```

`http://127.0.0.1:8000` now serves the new interface directly from your machine.
Because the page and the server are the same address, none of the browser's
cross-origin rules apply at all.

---

## When something goes wrong

**"Python was not found"** — PATH was not set during installation. Re-run the
Python installer, choose **Modify**, and tick "Add python.exe to PATH".

**`git clone` asks for a password and then fails** — your GitHub account has not
been added to the private repository yet.

**The page says it cannot reach the server**

- Is `microct-web` still running in its window?
- Open <http://127.0.0.1:8000/api/health> in a tab. JSON means the server is
  fine, and the problem is the `MICROCT_ALLOWED_ORIGINS` line from step 5.
- Check the address is exactly `https://microctweb.vercel.app`. A per-deployment
  address like `microctweb-a1b2c3.vercel.app` will not work — the check is exact
  and those change constantly.
- Restart the server after editing `.env`; it only reads it at startup.

**Everything I queue stays at "queued"** — the worker is not running. See step 6.

**Nothing works in Safari** — expected. Safari will not let a hosted HTTPS page
talk to your own machine. Use `http://127.0.0.1:8000`, or another browser.

**The interface loads but is empty** — that is correct for a fresh install. You
have no scans yet; see *Loading your first scan*.

---

## A word of caution about the measurements

The morphometry feature is built for **mouse terminal phalanx** scans at roughly
4 µm. Pointed at any other anatomy it does not fail — it produces a complete set
of confident, entirely ordinary-looking numbers that are meaningless.

Because of that, measurement is **gated on anatomy** and the gate is **on by
default**. A dataset must be tagged `phalanx` before it can be measured;
otherwise starting a measurement is refused outright, with a message naming the
dataset and the tag that would unblock it. Two settings in `.env` control it:

```ini
# Refuse to measure a dataset that is not marked as the right anatomy (default true)
MICROCT_MORPH_REQUIRE_ANATOMY=true

# Dataset tag(s) that count as "the right anatomy". Comma-separated, any one
# matches, case-insensitive. (default: phalanx)
MICROCT_MORPH_ANATOMY_TAGS=phalanx
```

Both are reported by `GET /api/config`, so the interface can explain a block and
name the required tag rather than assuming it. Set
`MICROCT_MORPH_REQUIRE_ANATOMY=false` to measure other anatomy anyway — do that
only if you intend to judge every number yourself.

Past the gate, the interface still checks each result against a reference range
and shows a warning banner, hiding the numbers behind an acknowledgement when
something looks wrong. **Treat that banner as a stop sign, not a suggestion.** If
you are measuring anything other than a phalanx, the numbers need a human to
confirm the segmentation is the right structure before they are used for
anything.
