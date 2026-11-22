# YouTube Channel Performance Analyzer

A Python data pipeline that tracks YouTube channels over time. Every day it
collects the 50 most recent videos from each channel on a watchlist through
the YouTube Data API, stores every reading in a SQLite database with a
timestamp, and then analyzes the history with pandas and matplotlib.

The point of storing history: the YouTube API can tell you a video has
4.2 million views, but not whether it is still growing. Comparing two
readings taken a day apart can — "this video gained 18,000 views in the last
24 hours" is a different and more useful answer than a single total.

![Views gained since tracking started, fastest 3 videos per channel](growth_chart.png)

*Views gained since tracking started for the three fastest-growing videos on
each channel. Every line starts at zero when tracking began, so the steepness
of a line is how fast that video is growing. Each panel has its own scale.*

## What it does

The project is three scripts, each with one job:

| Script | Job | Talks to YouTube? |
|---|---|---|
| `main.py` | **Collects.** Fetches channel and video data, calculates metrics, saves a snapshot of every video to `history.db`, and can build an HTML report for one channel. | Yes |
| `analyze.py` | **Analyzes.** Reads the database with pandas and prints comparison tables. | No |
| `charts.py` | **Visualizes.** Draws the growth chart above from the same data. | No |

Collection and analysis are kept separate on purpose. Collection runs
unattended every day; analysis only ever *reads* the database, so nothing
done while exploring the data can damage the history.

## The dataset so far

As of November 22, 2026:

- **2,952 readings** of **753 videos** across **15 channels**
- Collected since November 20, 2026, and growing daily
- **740 videos** have been read more than once, tracked for a median of
  57 hours

The database (`history.db`) is included in the repository, so `analyze.py`
and `charts.py` run straight after cloning — no API key needed.

## An early finding: the overall number can point the wrong way

Across all channels together, Shorts get slightly more views than long-form
videos (median 1.97M vs 1.83M). But inside **6 of the 8 channels with at
least 5 of each**, long-form videos get more views. On Veritasium, for example, the
median long-form video has 7.8M views and the median Short has 1.9M.

The overall result is pulled up by MrBeast, who posts 36 Shorts at around
144M views each. Combining every channel into one table mostly measures
*which channels post Shorts*, not how Shorts perform. This is known as
**Simpson's paradox**, and it is why `analyze.py` always shows a
per-channel breakdown alongside the overall one.

*Based on a few days of data — this section will be updated as the dataset
grows.*

## The metrics

The YouTube API returns raw counts. These are calculated from them.

**Engagement rate** — `((likes + comments) / views) × 100`

What percentage of viewers did something beyond watching. A raw like count
means nothing without knowing how many people saw the video.

**View velocity** — `views / hours since upload`

Average views per hour over the video's whole lifetime. Useful for comparing
recent uploads, but an old video always looks slow.

**Momentum** — `views gained / hours between two readings`

How fast a video is gaining views *right now*. This is what the database
makes possible: it compares the current reading with an earlier one instead
of averaging over the video's whole life.

**Performance index** — each video's views compared to the channel's recent
average, shown as `+43%` or `-38%`

A view count on its own doesn't tell you whether a video did well for *that*
channel. This does.

**Upload cadence** — average days between uploads

**Trend** — the recent average compared to the channel's all-time average
(lifetime views divided by total videos)

Videos are also labelled as Shorts (three minutes or shorter) or livestreams,
since both perform differently from regular uploads.

## Setup

You need Python 3.11 or newer.

**1. Clone the repository**

```bash
git clone https://github.com/kaveeshathirimadura/youtube-analyzer.git
cd youtube-analyzer
```

**2. Install the dependencies**

```bash
pip install -r requirements.txt
```

At this point `python analyze.py` and `python charts.py` already work on the
included data. The remaining steps are only needed to collect new data.

**3. Get a YouTube Data API key**

- Go to the [Google Cloud Console](https://console.cloud.google.com) and
  create a project.
- Enable **YouTube Data API v3** for that project.
- Under **Credentials**, create an API key.
- Restrict the key to YouTube Data API v3 only.

The API is free. Collecting one channel costs about 3 quota units against a
daily limit of 10,000, so tracking dozens of channels is well within it.

**4. Add your key**

Create a file named `.env` in the project folder:

```
YOUTUBE_API_KEY=your_key_here
```

This file is listed in `.gitignore` and is never committed.

## Usage

**Collecting data**

```
python main.py                      prompt for a handle, then open its report
python main.py "@mkbhd"             analyze one channel and open its report
python main.py --add "@mkbhd"       start tracking a channel
python main.py --remove "@mkbhd"    stop tracking (past readings are kept)
python main.py --list               show the watchlist and how much is collected
python main.py --all                collect every tracked channel, no report
python main.py --help
```

`@mkbhd`, `mkbhd`, and a pasted channel URL all work. On Windows PowerShell,
put quotes around handles that start with `@` — PowerShell treats a bare `@`
as special and silently drops the argument.

**Analyzing data**

```
python analyze.py                   print the comparison tables
python charts.py                    save growth_chart.png and open it
```

**Collecting automatically**

`--all` never stops to ask for input, so a scheduler can run it unattended.
On Windows, this registers a daily run with Task Scheduler (PowerShell):

```powershell
$folder = "C:\path\to\youtube-analyzer"
$action = New-ScheduledTaskAction -Execute "cmd.exe" `
    -Argument "/c .venv\Scripts\python.exe -X utf8 main.py --all >> collect.log 2>&1" `
    -WorkingDirectory $folder
$trigger = New-ScheduledTaskTrigger -Daily -At 8:00PM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "YouTube Analyzer Daily" `
    -Action $action -Trigger $trigger -Settings $settings
```

`-WorkingDirectory` matters: the database path is relative, and without it
Task Scheduler would start in `C:\Windows\System32` and write to a new, empty
database there. Each run's output is added to `collect.log`.

## How it works

**Collection** takes three chained API requests per channel — no single
endpoint returns a channel's recent videos with their statistics:

1. **`channels`** — looks up the channel by handle and returns its statistics
   plus the ID of its uploads playlist.
2. **`playlistItems`** — reads the 50 most recent video IDs from that
   playlist, following `nextPageToken` when more than one page is needed.
3. **`videos`** — fetches statistics for those IDs, in chunks of 50 (the
   API's limit per request). The API does not return videos in the order
   requested, so the results are put back into playlist order.

**Storage** is a SQLite database with three tables:

```
channels    one row per channel, refreshed each run
watchlist   which channels --all collects
snapshots   one row per video per run — never updated or deleted
```

`snapshots` is append-only, because the history is the whole value of the
project. Removing a channel from the watchlist stops collection but keeps its
past readings. All queries use `?` placeholders rather than building SQL from
strings.

**Analysis** in `analyze.py` makes a few deliberate choices:

- **One row per video.** A video read on five different days appears five
  times in `snapshots`. Before comparing groups, only each video's latest
  reading is kept, so no video is counted more than once.
- **Medians, not averages.** One viral video can drag an average far away
  from what a typical video does; it barely moves the median.
- **Small groups are hidden.** Any group with fewer than 5 videos is left out
  of the tables, and the output says how many were hidden.
- **Per-channel breakdowns.** Every format comparison is also shown inside
  each channel (see the finding above).
- **Age shown alongside momentum.** New videos gain views much faster than
  old ones, so each momentum figure is shown with the median age of the
  videos behind it.

`charts.py` imports its calculations from `analyze.py` instead of copying
them, so the tables and the chart can never disagree.

## Project structure

```
youtube-analyzer/
├── main.py            Collection: API requests, metrics, database, HTML report
├── analyze.py         Analysis: pandas comparison tables
├── charts.py          Visualization: the growth chart
├── style.css          Styles for the HTML report
├── history.db         The collected data (SQLite)
├── growth_chart.png   Output of charts.py
├── requirements.txt   Python dependencies
├── .env               Your API key (not committed)
├── .gitignore
└── README.md
```

## Error handling

Any function that fails prints a readable message saying what went wrong and
what to do about it, then returns `None`, and the caller stops. The program
handles a missing or rejected API key, an exhausted daily quota, connection
failures and timeouts, invalid handles, channels with no public videos,
missing fields in the API response, and a missing or empty database.

No situation produces a stack trace.

## Security notes

- The API key lives in `.env`, which is in `.gitignore`. It is never in the
  source code.
- Error messages are passed through a `redact()` function before printing.
  The `requests` library includes the full request URL in some of its error
  messages, and that URL contains the API key — without this, a network error
  could print the key to the terminal or to `collect.log`.
- User input is validated against YouTube's allowed handle characters before
  any request is sent.
- All values taken from the API are HTML-escaped before being written into
  the report, including values used inside HTML attributes.

## Limitations

Worth being clear about what these numbers do and don't tell you.

**The channels are hand-picked, not a random sample.** Findings describe
these 15 channels, not YouTube as a whole.

**The tracking window is short.** Collection started on November 20, 2026.
Momentum figures will become more reliable as more daily readings
accumulate.

**Momentum is an average between readings.** It shows the average rate
between two readings, not what happened hour by hour in between.

**Some tracked channels are dormant.** A few channels' most recent uploads
are years old, so their momentum is close to zero. That is accurate, but it
lowers any figure that combines all channels.

**View velocity favours new videos.** It averages views across a video's
whole lifetime, so an old video always shows a low figure. Momentum exists
to answer the "how is it doing now" question instead.

**The lifetime baseline is rough.** A channel that has run for ten years and
changed format twice has an all-time average that may not represent anything
current. Channels that post Shorts have their lifetime average inflated by
them.

**Shorts detection is inferred, not exact.** The API has no field marking a
video as a Short, so anything three minutes or shorter is treated as one. A
genuinely short regular video will be labelled incorrectly.

**Some metrics aren't publicly available.** Watch time, average view
duration, audience retention, click-through rate, and subscribers gained per
video live in the YouTube *Analytics* API, which requires OAuth authorisation
from the channel owner. They cannot be retrieved with an API key. Dislikes
were removed from the public API in 2021.

## Built with

- **Python** — the whole pipeline
- **YouTube Data API v3** — data source
- **SQLite** (`sqlite3`) — storing the reading history
- **pandas** — analysis
- **matplotlib** — charts
- **requests** — HTTP requests
- **python-dotenv** — loading the API key from `.env`
- **HTML / CSS** — the single-channel report

## What's next

- A written summary of findings once there are a few weeks of data
- Sorting buttons on the HTML report
- Longer term: a web app where anyone can add the channels they care about
  and see how they are performing over time, without using the command line
