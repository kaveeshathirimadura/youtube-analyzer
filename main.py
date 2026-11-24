import os
import sys
import html
import sqlite3
import webbrowser
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("YOUTUBE_API_KEY")

BASE_URL = "https://www.googleapis.com/youtube/v3"
OUTPUT_FILE = "report.html"
DATABASE_FILE = "history.db"

# YouTube's API policies say statistics collected with an API key (no
# channel-owner sign-in) must not be stored for more than 30 days. 29 leaves
# one day of margin, so a late or missed daily run can't push a reading past 30.
RETENTION_DAYS = 29

# How many recent videos to pull per channel. The API charges one unit per
# request regardless of how many items come back, so 50 costs the same as 5.
VIDEOS_TO_FETCH = 50

# The API refuses more than 50 items in a single request, so larger numbers
# are fetched one page at a time.
API_PAGE_SIZE = 50

# How many analyzed videos appear on the page. This used to be 10 to keep the
# page short, but the sort buttons make a long list easy to use, and showing
# all 50 means "Sort by Views" finds the channel's real top video.
DISPLAY_LIMIT = 50
REQUEST_TIMEOUT = 10

# characters allowed in a YT handle
ALLOWED_HANDLE_CHARS = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789._-"
)
MIN_HANDLE_LENGTH = 3
MAX_HANDLE_LENGTH = 30

# engagement rate cutoffs (for color coding)
HIGH_ENGAGEMENT = 3.5
MEDIUM_ENGAGEMENT = 2.0

# 100 = average (video performance vs. latest videos)
# 100-20+ = below-average, 100+20+ = above-average
INDEX_MARGIN = 20

# input and security helpers:
def redact(text):
    """Remove the API key from a string before it gets printed.

    The requests library includes the full request URL in some of its error
    messages, and our URL has the API key in the query string. Without this,
    a network error could print the key straight to the terminal.
    """
    if API_KEY:
        return text.replace(API_KEY, "[API KEY HIDDEN]")
    return text


def clean_handle(raw_input):
    """Validate user input and return a usable @handle, or None if invalid.

    Accepts '@channel', 'channel', or a pasted channel URL.
    """
    # YouTube treats @MrBeast and @mrbeast as the same channel, but SQLite
    # compares text exactly. Lowercasing here keeps one channel = one history.
    handle = raw_input.strip().lower()

    if handle == "":
        return None

    # pull the handle out of a pasted URL
    if "youtube.com/" in handle:
        handle = handle.split("youtube.com/")[-1]
        handle = handle.split("/")[0]
        handle = handle.split("?")[0]

    if not handle.startswith("@"):
        handle = "@" + handle

    # check if the handle after @ is a reasonable length
    name = handle[1:]
    if len(name) < MIN_HANDLE_LENGTH:
        return None
    if len(name) > MAX_HANDLE_LENGTH:
        return None

    # reject characters not allowed by YT (easier for API)
    for character in name:
        if character not in ALLOWED_HANDLE_CHARS:
            return None

    return handle

# API helpers:
# returns None if something went wrong after talking to API
# prints the error itself, so the caller just checks for None and stops
def describe_api_error(data):
    """Turn Google's error JSON into a sentence the user can act on."""
    error = data.get("error", {})
    message = error.get("message", "Unknown error")

    reason = ""
    details = error.get("errors")
    if details:
        reason = details[0].get("reason", "")

    if reason == "quotaExceeded":
        return ("Daily YouTube API quota is used up. Resets at 12:00 AM Pacific Time.")

    if reason == "keyInvalid" or reason == "badRequest":
        return ("The API key was rejected. Check the value your .env file and make sure YouTube Data API v3 is enabled for that project.")

    if reason == "accessNotConfigured":
        return ("YouTube Data API v3 isn't enabled for this API key's project. Enable it in Google Cloud Console then try again.")

    if reason == "forbidden":
        return "YouTube refused the request. Your API key may have restrictions."

    return "YouTube returned an error: " + redact(message)

def api_get(endpoint, params):
    """Send a GET request to the YouTube API.

    Returns the JSON data, or None if anything went wrong.
    """
    if not API_KEY:
        print("No API key found. Create a file named .env next to main.py.")
        print("Then type 'YOUTUBE_API_KEY=your_key_here' into the .env file.")
        return None

    params["key"] = API_KEY
    url = BASE_URL + "/" + endpoint

    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.Timeout:
        print("YouTube took too long to respond. Check your connection.")
        return None
    except requests.exceptions.ConnectionError:
        print("Couldn't reach YouTube. Check your internet connection.")
        return None
    except requests.exceptions.RequestException as error:
        print("This request failed: " + redact(str(error)))
        return None

    try:
        data = response.json()
    except ValueError:
        print("YouTube returned a response that wasn't valid JSON.")
        return None

    if "error" in data:
        print(describe_api_error(data))
        return None

    return data

def parse_duration(duration_string):
    """Turn YouTube's 'PT1H2M30S' format into a number of seconds.

    Walk through the text one character at a time. Digits get collected into
    a number, and a letter tells us what that number was measuring.
    """
    hours = 0
    minutes = 0
    seconds = 0
    number = ""

    for character in duration_string:
        if character.isdigit():
            number = number + character
        elif character == "H":
            hours = int(number)
            number = ""
        elif character == "M":
            minutes = int(number)
            number = ""
        elif character == "S":
            seconds = int(number)
            number = ""

    return hours * 3600 + minutes * 60 + seconds

def parse_published_date(date_string):
    """Turn '2026-08-14T15:00:00Z' into a datetime object.

    YouTube marks its times as UTC with a 'Z' on the end. Python's date
    reader doesn't understand that letter, but it does understand '+00:00',
    which means the same thing. So we swap one for the other first.

    The result knows it is in UTC. That matters because you can only subtract
    two datetimes if both of them know their timezone, and calculate_velocity
    does exactly that.
    """
    utc_date_string = date_string.replace("Z", "+00:00")
    return datetime.fromisoformat(utc_date_string)

def safe_int(value):
    """Convert an API value to an int, using 0 if it's missing or not a number."""
    if value is None:
        return 0

    try:
        return int(value)
    except ValueError:
        return 0

# fetching data:
def get_channel_data(handle):
    """Look up a channel by its @handle. Returns None if it can't be used."""
    data = api_get("channels", {
        "part": "snippet,statistics,contentDetails",
        "forHandle": handle,
    })

    if data is None:
        return None

    if not data.get("items"):
        print("No channel found for " + handle + ".")
        print("Please check the spelling, or copy the proper handle from the channel's page.")
        return None

    channel = data["items"][0]
    snippet = channel.get("snippet", {})
    stats = channel.get("statistics", {})
    content = channel.get("contentDetails", {})

    playlists = content.get("relatedPlaylists", {})
    uploads_playlist_id = playlists.get("uploads")

    if uploads_playlist_id is None:
        print("That channel has no public uploads playlist, nothing to analyze.")
        return None

    video_count = safe_int(stats.get("videoCount"))
    total_views = safe_int(stats.get("viewCount"))

    # channel's all-time average views per video compared to latest five videos
    if video_count > 0:
        lifetime_average_views = total_views / video_count
    else:
        lifetime_average_views = 0

    return {
        "name": snippet.get("title", handle),
        "handle": handle,
        "subscribers": safe_int(stats.get("subscriberCount")),
        "subscribers_hidden": stats.get("hiddenSubscriberCount", False),
        "video_count": video_count,
        "total_views": total_views,
        "lifetime_average_views": lifetime_average_views,
        "uploads_playlist_id": uploads_playlist_id,
    }

def get_recent_video_ids(playlist_id, count=VIDEOS_TO_FETCH):
    """Get the IDs of a channel's most recent videos. None if it failed.

    The API sends at most 50 items per request and includes a
    nextPageToken when there are more. Asking again with that token is
    how you get page two. The loop stops when we have enough videos, or
    when the API stops giving us a token because there are no more.
    """
    video_ids = []
    page_token = None

    while len(video_ids) < count:
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": API_PAGE_SIZE,
        }

        if page_token is not None:
            params["pageToken"] = page_token

        data = api_get("playlistItems", params)
        if data is None:
            return None

        items = data.get("items", [])
        if len(items) == 0:
            break

        for item in items:
            content = item.get("contentDetails", {})
            video_id = content.get("videoId")
            if video_id:
                video_ids.append(video_id)

        page_token = data.get("nextPageToken")
        if page_token is None:
            break

    # The last page may have taken us past what we asked for.
    return video_ids[:count]

def get_video_stats(video_ids):
    """Get the details for a list of video IDs. None if it failed."""
    if not video_ids:
        return []

    # The videos endpoint takes at most 50 ids at once, so longer lists are
    # split into chunks. Each chunk is still a single request, which is far
    # cheaper than one request per video.
    items = []
    start = 0

    while start < len(video_ids):
        chunk = video_ids[start:start + API_PAGE_SIZE]

        data = api_get("videos", {
            "part": "snippet,statistics,contentDetails,liveStreamingDetails",
            "id": ",".join(chunk),
        })

        if data is None:
            return None

        for item in data.get("items", []):
            items.append(item)

        start = start + API_PAGE_SIZE

    # API can send videos back in any order
    # latest videos first would be better so:
    # - put every video into a dictionary where the key is its ID.
    # - read them back out in the order we originally wanted.
    videos_by_id = {}

    for item in items:
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})

        duration = parse_duration(content.get("duration", ""))

        # YT sends different sizes for thumbnails nested inside each other (snippet -> thumbnails -> medium -> url)
        # unpacks one lvl at a time so a missing lvl doesn't crash
        thumbnails = snippet.get("thumbnails", {})
        medium_thumbnail = thumbnails.get("medium", {})
        thumbnail = medium_thumbnail.get("url", "")

        videos_by_id[item["id"]] = {
            "id": item["id"],
            "title": snippet.get("title", "Untitled"),
            "published_at": parse_published_date(snippet["publishedAt"]),
            "thumbnail": thumbnail,
            "views": safe_int(stats.get("viewCount")),
            "likes": safe_int(stats.get("likeCount")),
            "comments": safe_int(stats.get("commentCount")),
            "duration_seconds": duration,
            # This key only exists on videos that were streamed live.
            "is_live": "liveStreamingDetails" in item,
        }

    # build final list in the order of given playlist (newest first)
    videos = []
    for video_id in video_ids:
        if video_id in videos_by_id:
            videos.append(videos_by_id[video_id])

    return videos

# database:
# Every run records what each video looks like at that moment. Comparing two
# readings taken days apart is what turns "views so far" into "views gained
# recently", which is a different and more useful question.
def setup_database():
    """Create the tables if they don't exist yet.

    SQLite ships with Python, so there is nothing to install. The whole
    database is one file called history.db sitting next to main.py.
    """
    connection = sqlite3.connect(DATABASE_FILE)
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            handle TEXT PRIMARY KEY,
            name TEXT,
            subscribers INTEGER,
            video_count INTEGER,
            total_views INTEGER,
            last_checked TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            handle TEXT PRIMARY KEY,
            added_at TEXT
        )
    """)

    # is_short used to hold a guess that a video was a Short, based on its
    # length. YouTube's API has no such field, and its policies don't allow
    # inferring a video's type, so it is no longer filled in. The column
    # stays so existing databases keep working; analysis uses
    # duration_seconds, a value YouTube actually reports.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT,
            channel_handle TEXT,
            title TEXT,
            published_at TEXT,
            duration_seconds INTEGER,
            is_short INTEGER,
            is_live INTEGER,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            recorded_at TEXT
        )
    """)

    connection.commit()
    connection.close()

def delete_expired_data():
    """Delete readings older than RETENTION_DAYS. Returns how many rows went.

    main() calls this before every command, so the database only ever holds
    a rolling window of recent history: new readings come in, the oldest
    ones expire. Rows are only ever added or deleted, never edited.
    """
    setup_database()

    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)

    # recorded_at is stored as ISO text, and ISO text sorts in time order,
    # so "older than the cutoff" is a plain < comparison between strings.
    cutoff_text = cutoff.isoformat()

    connection = sqlite3.connect(DATABASE_FILE)
    cursor = connection.cursor()

    cursor.execute("DELETE FROM snapshots WHERE recorded_at < ?", (cutoff_text,))
    removed = cursor.rowcount

    # A channel that is no longer collected keeps its row in channels (name,
    # subscriber count). That data expires on the same schedule.
    cursor.execute("DELETE FROM channels WHERE last_checked < ?", (cutoff_text,))
    removed = removed + cursor.rowcount

    connection.commit()

    # DELETE only marks the space as free - the old bytes stay inside the
    # file until VACUUM rebuilds it. Running it means deleted data is gone.
    if removed > 0:
        connection.execute("VACUUM")

    connection.close()
    return removed

def get_previous_snapshot(video_id):
    """The most recent earlier reading for this video, or None if there is none.

    The question marks are placeholders. SQLite fills them in from the tuple
    passed as the second argument. Never build SQL by joining strings
    together with user data in them, which is how SQL injection happens.
    """
    connection = sqlite3.connect(DATABASE_FILE)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT views, recorded_at
        FROM snapshots
        WHERE video_id = ?
        ORDER BY recorded_at DESC
        LIMIT 1
    """, (video_id,))

    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None

    return {
        "views": row[0],
        "recorded_at": datetime.fromisoformat(row[1]),
    }

def save_snapshot(channel, videos):
    """Write one row per video recording how it looks right now."""
    connection = sqlite3.connect(DATABASE_FILE)
    cursor = connection.cursor()

    # SQLite has no date type, so dates are stored as text. ISO format
    # sorts correctly as text, which is why ORDER BY recorded_at works.
    recorded_at = datetime.now(timezone.utc).isoformat()

    # One row per channel, updated each run.
    cursor.execute("""
        INSERT OR REPLACE INTO channels
            (handle, name, subscribers, video_count, total_views, last_checked)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        channel["handle"],
        channel["name"],
        channel["subscribers"],
        channel["video_count"],
        channel["total_views"],
        recorded_at,
    ))

    # A new row per video per run, so the history builds up over time.
    for video in videos:
        cursor.execute("""
            INSERT INTO snapshots
                (video_id, channel_handle, title, published_at,
                 duration_seconds, is_short, is_live,
                 views, likes, comments, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            video["id"],
            channel["handle"],
            video["title"],
            video["published_at"].isoformat(),
            video["duration_seconds"],
            # is_short is no longer filled in (see setup_database), so new
            # rows store NULL - SQL's "no value" - in that column.
            None,
            int(video["is_live"]),
            video["views"],
            video["likes"],
            video["comments"],
            recorded_at,
        ))

    connection.commit()
    connection.close()

def get_watchlist():
    """Every handle being tracked, oldest first."""
    setup_database()

    connection = sqlite3.connect(DATABASE_FILE)
    cursor = connection.cursor()
    cursor.execute("SELECT handle FROM watchlist ORDER BY added_at")

    handles = []
    for row in cursor.fetchall():
        handles.append(row[0])

    connection.close()
    return handles

def add_to_watchlist(handle):
    """Start tracking a channel. Returns False if it was already there."""
    setup_database()

    if handle in get_watchlist():
        return False

    connection = sqlite3.connect(DATABASE_FILE)
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO watchlist (handle, added_at) VALUES (?, ?)",
        (handle, datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()
    connection.close()
    return True

def remove_from_watchlist(handle):
    """Stop tracking a channel. Returns False if it wasn't there.

    Past snapshots are not deleted here. They stay until they expire under
    RETENTION_DAYS, like every other reading.
    """
    setup_database()

    if handle not in get_watchlist():
        return False

    connection = sqlite3.connect(DATABASE_FILE)
    cursor = connection.cursor()
    cursor.execute("DELETE FROM watchlist WHERE handle = ?", (handle,))
    connection.commit()
    connection.close()
    return True

def get_history_summary():
    """One row per channel: how much data we hold and when we collected it.

    GROUP BY collapses every snapshot for a channel into a single row, and
    COUNT, MIN and MAX summarize the group. DISTINCT counts each video once
    no matter how many readings it has.
    """
    setup_database()

    connection = sqlite3.connect(DATABASE_FILE)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT channel_handle,
               COUNT(*),
               COUNT(DISTINCT video_id),
               COUNT(DISTINCT recorded_at),
               MIN(recorded_at),
               MAX(recorded_at)
        FROM snapshots
        GROUP BY channel_handle
        ORDER BY channel_handle
    """)

    rows = []
    for row in cursor.fetchall():
        rows.append({
            "handle": row[0],
            "readings": row[1],
            "videos": row[2],
            "collections": row[3],
            "first_seen": row[4],
            "last_seen": row[5],
        })

    connection.close()
    return rows

def count_snapshots():
    """How many readings the database holds in total."""
    connection = sqlite3.connect(DATABASE_FILE)
    cursor = connection.cursor()
    cursor.execute("SELECT COUNT(*) FROM snapshots")
    total = cursor.fetchone()[0]
    connection.close()
    return total

# calculations:
def calculate_engagement(views, likes, comments):
    """Percentage of viewers who liked or commented."""
    if views == 0:
        return 0.0
    return ((likes + comments) / views) * 100

def calculate_velocity(views, published_at):
    """Average views per hour since the video was uploaded."""
    # both datetimes must be timezone-aware to subtract (published_at is UTC)
    now = datetime.now(timezone.utc)

    difference = now - published_at
    hours_since_upload = difference.total_seconds() / 3600

    # views per hour floor at 1 hour (doesn't blow up the rate)
    if hours_since_upload < 1:
        hours_since_upload = 1

    return views / hours_since_upload

def calculate_momentum(current_views, previous):
    """Views gained per hour since the last time this program ran.

    Velocity averages across a video's whole life, so an old video always
    looks slow. This measures the rate right now instead. Returns None on
    the first run, because there is no earlier reading to compare against.
    """
    if previous is None:
        return None

    now = datetime.now(timezone.utc)
    difference = now - previous["recorded_at"]
    hours_between = difference.total_seconds() / 3600

    # Two readings less than an hour apart are too close to mean anything.
    if hours_between < 1:
        return None

    views_gained = current_views - previous["views"]
    return views_gained / hours_between

def calculate_average_views(videos):
    """Average view count across the recent videos."""
    if not videos:
        return 0

    total = 0
    for video in videos:
        total = total + video["views"]

    return total / len(videos)

def calculate_average_engagement(videos):
    """Average engagement rate across the recent videos."""
    if not videos:
        return 0

    total = 0
    for video in videos:
        total = total + video["engagement"]

    return total / len(videos)

def calculate_upload_cadence(videos):
    """Average number of days between uploads. None if there aren't enough."""
    if len(videos) < 2:
        return None

    dates = []
    for video in videos:
        dates.append(video["published_at"])
    dates.sort()

    gaps = []
    for i in range(1, len(dates)):
        gap = (dates[i] - dates[i - 1]).total_seconds() / 86400
        gaps.append(gap)

    total = 0
    for gap in gaps:
        total = total + gap

    return total / len(gaps)

def calculate_performance_index(views, average_views):
    """A video's views as a percentage of the recent average.

    100 means exactly average, 150 means 50% better than average.
    """
    if average_views == 0:
        return 0
    return (views / average_views) * 100

def calculate_trend(recent_average, lifetime_average):
    """How far recent views sit above or below the all-time average.

    Returns a percentage, or None if there's no baseline to compare to.
    """
    if lifetime_average == 0:
        return None
    return ((recent_average - lifetime_average) / lifetime_average) * 100

def engagement_level(engagement):
    """Bucket an engagement rate so the report can color-code it."""
    if engagement >= HIGH_ENGAGEMENT:
        return "high"
    if engagement >= MEDIUM_ENGAGEMENT:
        return "medium"
    return "low"

def index_level(index):
    """Bucket a performance index so the report can color-code it."""
    if index >= 100 + INDEX_MARGIN:
        return "high"
    if index <= 100 - INDEX_MARGIN:
        return "low"
    return "medium"

# formatting:
def plural(count, word):
    """1, 'video' -> '1 video'   |   3, 'video' -> '3 videos'"""
    if count == 1:
        return "1 " + word
    return str(count) + " " + word + "s"

def format_number(number):
    """Add thousands separators: 1234567 becomes '1,234,567'.

    The ':,' below is Python's built-in way of asking for those commas.
    """
    whole_number = round(number)
    return f"{whole_number:,}"

def format_percent(number):
    """4.5678 becomes '4.57%'"""
    return str(round(number, 2)) + "%"

def format_momentum(momentum):
    """Views per hour since the last run, or a dash if we don't know yet."""
    if momentum is None:
        return "&mdash;"
    return format_number(momentum) + "/hr"

def format_index(index):
    """Turn a performance index into a difference: 143 becomes '+43%'."""
    difference = round(index - 100)

    if difference > 0:
        return "+" + str(difference) + "%"
    return str(difference) + "%"

def pad_two_digits(number):
    """5 becomes '05', 12 stays '12'. Used for clock-style times."""
    if number < 10:
        return "0" + str(number)
    return str(number)

def format_duration(seconds):
    """90 becomes '1:30', 3725 becomes '1:02:05'"""
    hours = int(seconds / 3600)
    seconds_left = seconds - (hours * 3600)

    minutes = int(seconds_left / 60)
    remaining_seconds = seconds_left - (minutes * 60)

    if hours > 0:
        return (str(hours) + ":" + pad_two_digits(minutes)
                + ":" + pad_two_digits(remaining_seconds))

    return str(minutes) + ":" + pad_two_digits(remaining_seconds)

def format_time_ago(published_at):
    """Turn a datetime into a phrase like '3 days ago'."""
    difference = datetime.now(timezone.utc) - published_at
    hours = difference.total_seconds() / 3600

    if hours < 1:
        return "less than an hour ago"

    if hours < 24:
        count = int(hours)
        if count == 1:
            return "1 hour ago"
        return str(count) + " hours ago"

    days = int(hours / 24)
    if days == 1:
        return "1 day ago"
    return str(days) + " days ago"

def format_trend(trend):
    """Turn a trend percentage into a readable phrase."""
    if trend is None:
        return "with no all-time average to compare against"

    rounded = round(trend)

    if rounded > -5 and rounded < 5:
        return "in line with the channel's all-time average"

    if rounded > 0:
        return str(rounded) + "% above the channel's all-time average"

    return str(-rounded) + "% below the channel's all-time average"

def format_cadence(cadence):
    """Turn an average gap in days into a readable phrase."""
    if cadence is None:
        return "Not enough videos"
    return "Every " + str(round(cadence, 1)) + " days"

def data_value(number):
    """Write a number into a data- attribute so sort.js can sort by it.

    Unknown values (momentum before the second run) become an empty string,
    which sort.js puts at the bottom of the list.
    """
    if number is None:
        return ""
    return str(round(number, 4))

# report generation:
def build_tags(video):
    """Build the small labels shown next to a video's upload date.

    Only labels YouTube reports directly are shown. There is no "Short" tag:
    the API has no field for it, and the video's length is already shown.
    """
    tags = ""
    if video["is_live"]:
        tags = tags + '<span class="tag">Livestream</span>'
    return tags

def build_video_section(video, position):
    """Build the HTML for one video row.

    position is the row's place in upload order (0 = newest). It is stored on
    the row so the "Newest" button can put the original order back.
    """
    # Everything that came from the API gets escaped before it goes into the
    # page, including values used inside attributes.
    title = html.escape(video["title"])
    thumbnail = html.escape(video["thumbnail"])
    video_url = html.escape("https://www.youtube.com/watch?v=" + video["id"])

    engagement_class = "engagement-" + engagement_level(video["engagement"])
    index_class = "index-" + index_level(video["index"])

    uploaded = format_time_ago(video["published_at"])
    length = format_duration(video["duration_seconds"])
    index_value = format_index(video["index"])
    momentum_value = format_momentum(video["momentum"])

    return f"""
        <article class="video"
            data-position="{position}"
            data-views="{data_value(video['views'])}"
            data-index="{data_value(video['index'])}"
            data-engagement="{data_value(video['engagement'])}"
            data-velocity="{data_value(video['velocity'])}"
            data-momentum="{data_value(video['momentum'])}">
            <a class="thumb" href="{video_url}">
                <img src="{thumbnail}" alt="Thumbnail for {title}" loading="lazy">
            </a>
            <div class="video-body">
                <h3><a href="{video_url}">{title}</a></h3>
                <p class="meta">
                    Uploaded {uploaded} &middot; {length}
                    {build_tags(video)}
                </p>
                <div class="metrics">
                    <div class="metric">
                        <span class="label">Views</span>
                        <span class="value">{format_number(video['views'])}</span>
                    </div>
                    <div class="metric">
                        <span class="label">Vs. average</span>
                        <span class="value {index_class}">{index_value}</span>
                    </div>
                    <div class="metric">
                        <span class="label">Likes</span>
                        <span class="value">{format_number(video['likes'])}</span>
                    </div>
                    <div class="metric">
                        <span class="label">Comments</span>
                        <span class="value">{format_number(video['comments'])}</span>
                    </div>
                    <div class="metric">
                        <span class="label">Engagement</span>
                        <span class="value {engagement_class}">{format_percent(video['engagement'])}</span>
                    </div>
                    <div class="metric">
                        <span class="label">Velocity</span>
                        <span class="value">{format_number(video['velocity'])}/hr</span>
                    </div>
                    <div class="metric">
                        <span class="label">Momentum</span>
                        <span class="value">{momentum_value}</span>
                    </div>
                </div>
            </div>
        </article>
    """

def generate_html_report(channel, videos, summary):
    """Build report.html. Returns the filename, or None if writing failed."""

    if channel["subscribers_hidden"]:
        subscriber_text = "Subscribers hidden"
    else:
        subscriber_text = format_number(channel["subscribers"]) + " subscribers"

    cadence_text = format_cadence(summary["cadence"])
    trend_text = format_trend(summary["trend"])

    channel_name = html.escape(channel["name"])
    channel_handle = html.escape(channel["handle"])

    average_views_text = format_number(summary["average_views"])
    lifetime_text = format_number(channel["lifetime_average_views"])
    video_count_text = format_number(channel["video_count"])
    total_views_text = format_number(channel["total_views"])
    average_engagement_text = format_percent(summary["average_engagement"])
    video_total = len(videos)

    # Every video was analyzed, but only the newest few are shown.
    displayed = videos[:DISPLAY_LIMIT]

    if video_total > len(displayed):
        videos_heading = (str(len(displayed)) + " most recent of "
                          + str(video_total) + " analyzed")
    else:
        videos_heading = str(video_total) + " most recent videos"

    video_sections = ""
    position = 0
    for video in displayed:
        video_sections = video_sections + build_video_section(video, position)
        position = position + 1

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{channel_name} - Performance Report</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Roboto+Mono:wght@400;500&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <div class="page">
        <header class="channel">
            <p class="app-name">YouTube Channel Performance Analyzer</p>
            <h1>{channel_name}</h1>
            <p class="handle">{channel_handle}</p>
            <p class="subs">
                {subscriber_text}
                &middot; {video_count_text} videos
                &middot; {total_views_text} total views
            </p>
        </header>

        <p class="headline">
            The last {video_total} uploads averaged
            <strong>{average_views_text}</strong> views,
            {trend_text} ({lifetime_text} views per video).
        </p>

        <section class="summary">
            <div class="stat">
                <span class="label">Average views</span>
                <span class="figure">{average_views_text}</span>
            </div>
            <div class="stat">
                <span class="label">Average engagement</span>
                <span class="figure">{average_engagement_text}</span>
            </div>
            <div class="stat">
                <span class="label">Upload cadence</span>
                <span class="figure">{cadence_text}</span>
            </div>
        </section>

        <h2>{videos_heading}</h2>

        <!-- Each button calls sortVideos() in sort.js. data-sort names the
             data- attribute it sorts by, so sort.js knows which button to
             mark as pressed. -->
        <div class="sort-bar">
            <span class="label">Sort by</span>
            <button type="button" class="sort-button" data-sort="position" aria-pressed="true" onclick="sortVideos('position')">Newest</button>
            <button type="button" class="sort-button" data-sort="views" aria-pressed="false" onclick="sortVideos('views')">Views</button>
            <button type="button" class="sort-button" data-sort="index" aria-pressed="false" onclick="sortVideos('index')">Vs. average</button>
            <button type="button" class="sort-button" data-sort="engagement" aria-pressed="false" onclick="sortVideos('engagement')">Engagement</button>
            <button type="button" class="sort-button" data-sort="velocity" aria-pressed="false" onclick="sortVideos('velocity')">Velocity</button>
            <button type="button" class="sort-button" data-sort="momentum" aria-pressed="false" onclick="sortVideos('momentum')">Momentum</button>
        </div>

        <div id="video-list">
            {video_sections}
        </div>

        <p class="footer">
            "Vs. average" compares a videos view difference from the average views of these last {video_total} uploads.<br>
            "Engagement" shows the percent of viewers who liked or commented.<br>
            "Velocity" is the average views per hour since the video was uploaded.<br>
            "Momentum" is views per hour since this program last checked, and is blank until it has run twice.
        </p>
    </div>
    <script src="sort.js"></script>
</body>
</html>
"""

    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
            file.write(page)
    except OSError as error:
        print("Couldn't write " + OUTPUT_FILE + ": " + str(error))
        return None

    return OUTPUT_FILE

# main program:
def analyze_channel(handle, make_report=True):
    """Fetch, record and optionally report on one channel.

    Returns True if it worked. make_report is False in batch mode, where
    the point is collecting data rather than looking at a page.
    """
    print("Finding channel...")
    channel = get_channel_data(handle)
    if channel is None:
        return False

    print("Fetching latest videos...")
    video_ids = get_recent_video_ids(channel["uploads_playlist_id"])
    if video_ids is None:
        return False

    videos = get_video_stats(video_ids)
    if videos is None:
        return False

    if len(videos) == 0:
        print(channel["name"] + " has no public videos to analyze.")
        return False

    print("Checking history...")
    setup_database()

    # Look up each video's previous reading BEFORE saving the new one,
    # otherwise we would just be comparing a video against itself.
    for video in videos:
        previous = get_previous_snapshot(video["id"])
        video["momentum"] = calculate_momentum(video["views"], previous)

    save_snapshot(channel, videos)

    print("Calculating performance...")
    average_views = calculate_average_views(videos)

    for video in videos:
        video["engagement"] = calculate_engagement(
            video["views"], video["likes"], video["comments"]
        )
        video["velocity"] = calculate_velocity(
            video["views"], video["published_at"]
        )
        video["index"] = calculate_performance_index(
            video["views"], average_views
        )

    summary = {
        "average_views": average_views,
        "average_engagement": calculate_average_engagement(videos),
        "cadence": calculate_upload_cadence(videos),
        "trend": calculate_trend(average_views, channel["lifetime_average_views"]),
    }

    if not make_report:
        print("Recorded " + str(len(videos)) + " videos for " + channel["name"] + ".")
        return True

    print("Generating report...")
    report_path = generate_html_report(channel, videos, summary)
    if report_path is None:
        return False

    print("Report created: " + report_path)
    webbrowser.open("file://" + os.path.abspath(report_path))
    return True

# command line:
# Reading sys.argv lets the program be run by a scheduler without anyone
# sitting there to type an answer to input().
def print_usage():
    # Handles are shown in quotes because PowerShell treats a bare @ as its
    # splatting operator and swallows the argument before Python sees it.
    print("Usage:")
    print('  python main.py                     analyze one channel (asks for a handle)')
    print('  python main.py "@channel"            analyze that channel')
    print('  python main.py --all               collect data for every tracked channel')
    print('  python main.py --add "@channel"      start tracking a channel')
    print('  python main.py --remove "@channel"   stop tracking a channel')
    print('  python main.py --list              show tracked channels')
    print("")
    print("The quotes matter on Windows. Leaving the @ off works too.")

def run_watchlist():
    """Collect a reading for every tracked channel. No report."""
    handles = get_watchlist()

    if len(handles) == 0:
        print("Nothing is being tracked yet.")
        print("Add a channel with:  python main.py --add @channel")
        return

    print("Collecting data for " + str(len(handles)) + " channels...")
    print("")

    worked = 0
    for handle in handles:
        print("--- " + handle + " ---")
        if analyze_channel(handle, make_report=False):
            worked = worked + 1
        print("")

    print("Done. " + str(worked) + " of " + str(len(handles)) + " succeeded.")
    print("History: " + str(count_snapshots()) + " readings in " + DATABASE_FILE)

def main():
    # Expired readings are removed first, whatever command was given, so the
    # 29-day limit holds even if only --list or a single channel is ever run.
    removed = delete_expired_data()
    if removed > 0:
        print("Removed " + plural(removed, "row") + " older than "
              + str(RETENTION_DAYS) + " days (YouTube API data policy).")
        print("")

    args = sys.argv[1:]

    if len(args) == 0:
        raw_handle = input("Enter a YouTube handle (example: @channel): ")

        # Options like --all belong on the command line, not typed in here.
        # Without this check they would be treated as a channel name and sent
        # to YouTube, which produces a confusing "no channel found" message.
        if raw_handle.strip().startswith("-"):
            print("")
            print("Options go on the command line, not at this prompt.")
            print("Close this and run it as one command instead, for example:")
            print("    python main.py " + raw_handle.strip())
            print("")
            print_usage()
            return

        handle = clean_handle(raw_handle)
        if handle is None:
            print("That doesn't look like a YouTube handle. Try something like @channel.")
            return
        analyze_channel(handle)
        print("History: " + str(count_snapshots()) + " readings in " + DATABASE_FILE)
        return

    command = args[0]

    if command == "--help" or command == "-h":
        print_usage()
        return

    if command == "--all":
        run_watchlist()
        return

    if command == "--list":
        handles = get_watchlist()
        if len(handles) == 0:
            print("Nothing is being tracked yet.")
            print("Add a channel with:  python main.py --add @channel")
            return
        if len(handles) == 1:
            print("Tracking 1 channel:")
        else:
            print("Tracking " + str(len(handles)) + " channels:")
        for handle in handles:
            print("  " + handle)
        print("")

        summary = get_history_summary()
        if len(summary) == 0:
            print("No data collected yet. Run:  python main.py --all")
            return

        print("Collected so far:")
        for row in summary:
            print("  " + row["handle"]
                  + " - " + plural(row["videos"], "video")
                  + ", " + plural(row["collections"], "collection")
                  + ", " + plural(row["readings"], "reading"))
            print("      newest reading " + format_time_ago(
                parse_published_date(row["last_seen"])))

        print("")
        print(str(count_snapshots()) + " readings total in " + DATABASE_FILE)
        return

    if command == "--add" or command == "--remove":
        if len(args) < 2:
            print("Which channel?")
            print('Example:  python main.py ' + command + ' "@channel"')
            print("")
            print("If you did type a handle, your terminal may have eaten the @.")
            print("Put it in quotes, or leave the @ off entirely.")
            return

        handle = clean_handle(args[1])
        if handle is None:
            print("That doesn't look like a YouTube handle. Try something like @channel.")
            return

        if command == "--add":
            if add_to_watchlist(handle):
                print("Now tracking " + handle + ".")
                print("Collect a reading with:  python main.py --all")
            else:
                print(handle + " is already being tracked.")
        else:
            if remove_from_watchlist(handle):
                print("Stopped tracking " + handle + ". Past readings stay until they expire ("
                      + str(RETENTION_DAYS) + " days).")
            else:
                print(handle + " wasn't being tracked.")
        return

    if command.startswith("-"):
        print("Don't know the option " + command + ".")
        print("")
        print_usage()
        return

    # Anything else is treated as a handle.
    handle = clean_handle(command)
    if handle is None:
        print("That doesn't look like a YouTube handle. Try something like @channel.")
        return

    analyze_channel(handle)
    print("History: " + str(count_snapshots()) + " readings in " + DATABASE_FILE)

if __name__ == "__main__":
    main()

