import os
import html
import webbrowser
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("YOUTUBE_API_KEY")

BASE_URL = "https://www.googleapis.com/youtube/v3"
OUTPUT_FILE = "report.html"
REQUEST_TIMEOUT = 10

# characters allowed in a YT handle
ALLOWED_HANDLE_CHARS = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789._-"
)
MIN_HANDLE_LENGTH = 3
MAX_HANDLE_LENGTH = 30

# threshold for YT shorts
SHORT_MAX_SECONDS = 180

# engagement rate cutoffs (for color coding)
HIGH_ENGAGEMENT = 5.0
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

    Accepts '@mkbhd', 'mkbhd', or a pasted channel URL.
    """
    handle = raw_input.strip()

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

def get_recent_video_ids(playlist_id, count=5):
    """Get the IDs of a channel's most recent videos. None if it failed.

    count=5 is a default value. Calling get_recent_video_ids(playlist_id)
    gets 5 videos; calling get_recent_video_ids(playlist_id, 10) gets 10.
    """
    data = api_get("playlistItems", {
        "part": "contentDetails",
        "playlistId": playlist_id,
        "maxResults": count,
    })

    if data is None:
        return None

    video_ids = []
    for item in data.get("items", []):
        content = item.get("contentDetails", {})
        video_id = content.get("videoId")
        if video_id:
            video_ids.append(video_id)

    return video_ids

def get_video_stats(video_ids):
    """Get the details for a list of video IDs. None if it failed."""
    if not video_ids:
        return []

    data = api_get("videos", {
        "part": "snippet,statistics,contentDetails,liveStreamingDetails",
        "id": ",".join(video_ids),
    })

    if data is None:
        return None

    # API can send videos back in any order
    # latest videos first would be better so:
    # - put every video into a dictionary where the key is its ID.
    # - read them back out in the order we originally wanted.
    videos_by_id = {}

    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})

        duration = parse_duration(content.get("duration", ""))

        # YT sends different sizes for thumbnails nested inside each other (snippet -> thumbnails -> medium -> url)
        # unpacks one lvl at a time so a missing lvl doesn't crash
        thumbnails = snippet.get("thumbnails", {})
        medium_thumbnail = thumbnails.get("medium", {})
        thumbnail = medium_thumbnail.get("url", "")

        is_short = duration > 0 and duration <= SHORT_MAX_SECONDS

        videos_by_id[item["id"]] = {
            "id": item["id"],
            "title": snippet.get("title", "Untitled"),
            "published_at": parse_published_date(snippet["publishedAt"]),
            "thumbnail": thumbnail,
            "views": safe_int(stats.get("viewCount")),
            "likes": safe_int(stats.get("likeCount")),
            "comments": safe_int(stats.get("commentCount")),
            "duration_seconds": duration,
            "is_short": is_short,
            # This key only exists on videos that were streamed live.
            "is_live": "liveStreamingDetails" in item,
        }

    # build final list in the order of given playlist (newest first)
    videos = []
    for video_id in video_ids:
        if video_id in videos_by_id:
            videos.append(videos_by_id[video_id])

    return videos

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
def format_number(number):
    """Add thousands separators: 1234567 becomes '1,234,567'.

    The ':,' below is Python's built-in way of asking for those commas.
    """
    whole_number = round(number)
    return f"{whole_number:,}"

def format_percent(number):
    """4.5678 becomes '4.57%'"""
    return str(round(number, 2)) + "%"

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

# report generation:
def build_tags(video):
    """Build the small labels shown next to a video's upload date."""
    tags = ""
    if video["is_short"]:
        tags = tags + '<span class="tag">Short</span>'
    if video["is_live"]:
        tags = tags + '<span class="tag">Livestream</span>'
    return tags

def build_video_section(video):
    """Build the HTML for one video row."""
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

    return f"""
        <article class="video">
            <a class="thumb" href="{video_url}">
                <img src="{thumbnail}" alt="Thumbnail for {title}">
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

    video_sections = ""
    for video in videos:
        video_sections = video_sections + build_video_section(video)

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{channel_name} - Performance Report</title>
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

        <h2>Recent videos</h2>
        {video_sections}

        <p class="footer">
            "Vs. average" compares a videos view difference from the average views of these last {video_total} uploads.<br>
            "Engagement" shows the percent of viewers who liked or commented.<br>
            "Velocity" is the average views per hour since the video was uploaded.
        </p>
    </div>
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
def main():
    raw_handle = input("Enter a YouTube handle (example: @mkbhd): ")
    handle = clean_handle(raw_handle)

    if handle is None:
        print("That doesn't look like a YouTube handle. Try something like @mkbhd.")
        return

    print("Finding channel...")
    channel = get_channel_data(handle)
    if channel is None:
        return

    print("Fetching latest videos...")
    video_ids = get_recent_video_ids(channel["uploads_playlist_id"])
    if video_ids is None:
        return

    videos = get_video_stats(video_ids)
    if videos is None:
        return

    if len(videos) == 0:
        print(channel["name"] + " has no public videos to analyze.")
        return

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

    print("Generating report...")
    report_path = generate_html_report(channel, videos, summary)
    if report_path is None:
        return

    print("Report created: " + report_path)
    webbrowser.open("file://" + os.path.abspath(report_path))

if __name__ == "__main__":
    main()

