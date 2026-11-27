"""Create sample.db: a clearly labelled SYNTHETIC dataset for demonstrations.

Nothing in sample.db comes from YouTube. Every channel, video and number is
invented by this script. That means it isn't YouTube API data, so it can be
used to demonstrate the features that are switched off for real data
(engagement, performance index, cross-channel comparisons) and it is safe to
publish.

sample.db uses exactly the same tables as history.db, so analyze.py and
charts.py read it with the same code. A dataset_info table inside the file
records that it is synthetic, and the analysis checks that label before
switching the advanced metrics on.

Run it from the project folder:
    python make_sample_data.py
"""
import os
import math
import random
import sqlite3
from datetime import datetime, timezone, timedelta

import main

SAMPLE_FILE = "sample.db"

# A fixed seed makes random give the same "random" numbers every run, so
# sample.db comes out identical each time. That is what makes it testable:
# the same input always gives the same answers.
RANDOM_SEED = 42

# Fixed dates (not "now") for the same reason: the output never changes.
FIRST_READING = datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc)
READINGS = 10
HOURS_BETWEEN_READINGS = 24

# Each invented channel: how many videos of each length, roughly how many
# views a video ends up with, and its engagement rate. The numbers are chosen
# to show Simpson's paradox: Channel A is huge and posts mostly short videos,
# so pooled together short videos look best. Inside channels A and E short
# videos really do win, but inside B, C, D and F the longer videos do.
CHANNELS = [
    {"handle": "@samplechannela", "name": "Sample Channel A",
     "short_videos": 40, "short_views": 50000000,
     "long_videos": 10, "long_views": 40000000,
     "live_videos": 0, "engagement": 2.0},
    {"handle": "@samplechannelb", "name": "Sample Channel B",
     "short_videos": 6, "short_views": 200000,
     "long_videos": 24, "long_views": 1500000,
     "live_videos": 0, "engagement": 4.5},
    {"handle": "@samplechannelc", "name": "Sample Channel C",
     "short_videos": 5, "short_views": 80000,
     "long_videos": 25, "long_views": 600000,
     "live_videos": 0, "engagement": 6.0},
    {"handle": "@samplechanneld", "name": "Sample Channel D",
     "short_videos": 8, "short_views": 400000,
     "long_videos": 22, "long_views": 2000000,
     "live_videos": 0, "engagement": 3.0},
    {"handle": "@samplechannele", "name": "Sample Channel E",
     "short_videos": 14, "short_views": 900000,
     "long_videos": 16, "long_views": 300000,
     "live_videos": 0, "engagement": 5.0},
    {"handle": "@samplechannelf", "name": "Sample Channel F",
     "short_videos": 5, "short_views": 150000,
     "long_videos": 21, "long_views": 800000,
     "live_videos": 4, "engagement": 3.5},
]

# How many days it takes a video to get most of its views. Views rise fast
# after upload and then level off, like real videos do.
GROWTH_DAYS = 7


def views_at(final_views, age_days):
    """Views a video has at a given age, following a levelling-off curve.

    1 - e^(-age / GROWTH_DAYS) starts at 0 on upload day, reaches about 63%
    after GROWTH_DAYS days, and creeps towards 100% after that.
    """
    if age_days <= 0:
        return 0
    share = 1 - math.exp(-age_days / GROWTH_DAYS)
    return int(final_views * share)


def make_videos(channel):
    """Invent the videos for one channel. Returns a list of dictionaries."""
    videos = []
    kinds = []
    for count in range(channel["short_videos"]):
        kinds.append("short")
    for count in range(channel["long_videos"]):
        kinds.append("long")
    for count in range(channel["live_videos"]):
        kinds.append("live")

    number = 1
    for kind in kinds:
        if kind == "short":
            duration = random.randint(20, 175)
            typical_views = channel["short_views"]
        elif kind == "long":
            duration = random.randint(300, 1800)
            typical_views = channel["long_views"]
        else:
            duration = random.randint(3600, 10800)
            typical_views = channel["long_views"]

        # Each video lands somewhere between half and double the typical
        # number, so the channel has spread like a real one.
        final_views = int(typical_views * random.uniform(0.5, 2.0))

        # Uploaded at some point in the 60 days before the first reading.
        published = FIRST_READING - timedelta(days=random.uniform(1, 60))

        # Engagement varies a little around the channel's rate.
        engagement = channel["engagement"] * random.uniform(0.8, 1.2)

        videos.append({
            "id": channel["handle"][1:] + "-video-" + str(number),
            # Short titles fit in chart legends; each chart panel already
            # names the channel.
            "title": "Sample video " + str(number),
            "published_at": published,
            "duration_seconds": duration,
            "is_live": kind == "live",
            "final_views": final_views,
            "engagement": engagement,
        })
        number = number + 1
    return videos


def write_sample_database():
    """Build sample.db from scratch. Returns how many readings were written."""
    if os.path.exists(SAMPLE_FILE):
        # Rebuilding means starting from an empty file, so old sample data
        # can't mix with new. This only ever touches sample.db.
        os.remove(SAMPLE_FILE)

    # main.py's setup_database() creates the tables in whatever file
    # main.DATABASE_FILE names. Pointing it at sample.db means sample.db gets
    # exactly the same tables as history.db, from the same code.
    main.DATABASE_FILE = SAMPLE_FILE
    main.setup_database()

    random.seed(RANDOM_SEED)

    connection = sqlite3.connect(SAMPLE_FILE)
    cursor = connection.cursor()

    # The label the analysis checks before treating this file as synthetic.
    cursor.execute("CREATE TABLE dataset_info (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute("INSERT INTO dataset_info VALUES (?, ?)", ("source", "synthetic"))
    cursor.execute("INSERT INTO dataset_info VALUES (?, ?)",
                   ("note", "Invented by make_sample_data.py. Not from YouTube."))

    rows_written = 0
    for channel in CHANNELS:
        videos = make_videos(channel)

        last_reading = FIRST_READING + timedelta(hours=HOURS_BETWEEN_READINGS * (READINGS - 1))
        total_views = 0
        for video in videos:
            age = (last_reading - video["published_at"]).total_seconds() / 86400
            total_views = total_views + views_at(video["final_views"], age)

        cursor.execute("""
            INSERT INTO channels
                (handle, name, subscribers, video_count, total_views, last_checked)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (channel["handle"], channel["name"], 1000000, len(videos),
              total_views, last_reading.isoformat()))

        for reading in range(READINGS):
            recorded = FIRST_READING + timedelta(hours=HOURS_BETWEEN_READINGS * reading)
            for video in videos:
                age = (recorded - video["published_at"]).total_seconds() / 86400
                views = views_at(video["final_views"], age)
                likes = int(views * video["engagement"] / 100 * 0.9)
                comments = int(views * video["engagement"] / 100 * 0.1)

                cursor.execute("""
                    INSERT INTO snapshots
                        (video_id, channel_handle, title, published_at,
                         duration_seconds, is_short, is_live,
                         views, likes, comments, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (video["id"], channel["handle"], video["title"],
                      video["published_at"].isoformat(), video["duration_seconds"],
                      None, int(video["is_live"]), views, likes, comments,
                      recorded.isoformat()))
                rows_written = rows_written + 1

    connection.commit()
    connection.close()
    return rows_written


if __name__ == "__main__":
    written = write_sample_database()
    print("Created " + SAMPLE_FILE + " with " + str(written) + " readings.")
    print("SAMPLE DATA - invented by this script, not from YouTube.")
    print("Try:  python analyze.py --sample   or   python charts.py --sample")
