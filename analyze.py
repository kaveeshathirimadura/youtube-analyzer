"""Study the readings that main.py has collected in history.db.

main.py collects data. This file only reads it. Keeping the two apart means
nothing done during analysis can ever damage the history.

Run it from the project folder:
    python analyze.py
"""
import os
import sqlite3
import pandas as pd

DATABASE_FILE = "history.db"

# A group with only one or two videos says almost nothing: one unusual video
# becomes the whole result. Groups smaller than this are left out of tables.
MIN_GROUP_SIZE = 5

# Same rule as main.py: readings less than an hour apart are too close
# together for the difference to mean anything.
MIN_HOURS_BETWEEN = 1

# Videos are grouped by length, using duration_seconds as YouTube reports
# it. This is a fact about each video, not a guess about what kind of video
# it is (YouTube's API has no "Short" field, and its policies don't allow
# inferring one). 180 seconds keeps the same cutoff the old label used.
SHORT_LENGTH_SECONDS = 180


def load_snapshots():
    """Read every row of the snapshots table into a DataFrame, or None."""
    # sqlite3.connect() quietly creates an empty database if the file is
    # missing, which would make the query below fail with a confusing error.
    # Checking first lets us say what is actually wrong.
    if not os.path.exists(DATABASE_FILE):
        print("Could not find " + DATABASE_FILE + " in this folder.")
        print("Run this from the youtube-analyzer folder, and run main.py at least once first.")
        return None

    connection = sqlite3.connect(DATABASE_FILE)
    snapshots = pd.read_sql_query("SELECT * FROM snapshots", connection)
    connection.close()

    if len(snapshots) == 0:
        print("The database has no readings yet.")
        print("Collect some first with:  python main.py --all")
        return None

    # Early test runs saved handles like @MrBeast before main.py started
    # lowercasing them. Lowercasing here merges those rows with @mrbeast.
    # This changes the DataFrame only - the database itself is never edited.
    snapshots["channel_handle"] = snapshots["channel_handle"].str.lower()
    return snapshots


def latest_reading_per_video(snapshots):
    """Keep only the newest reading of each video.

    A video recorded on three runs appears in snapshots three times. Using
    every row would count that video three times in any average, so each
    video gets exactly one row: its most recent reading.
    """
    # ISO 8601 dates sort correctly as text, so newest-first is a plain sort.
    newest_first = snapshots.sort_values("recorded_at", ascending=False)
    videos = newest_first.drop_duplicates(subset="video_id", keep="first")
    return videos


def add_format_column(videos):
    """Group each video as a Livestream, '3 min or less', or 'Over 3 min'."""
    # Start by putting everything in one group, then overwrite the rows that
    # belong somewhere else. .loc[condition, column] means "these rows, this
    # column".
    videos["format"] = "Over 3 min"
    is_short_length = videos["duration_seconds"] <= SHORT_LENGTH_SECONDS
    videos.loc[is_short_length, "format"] = "3 min or less"

    # Livestreams are checked last so they win: a stream is its own thing,
    # whatever its length. The livestream flag comes from a field YouTube
    # actually returns, so it is a fact, not a guess.
    videos.loc[videos["is_live"] == 1, "format"] = "Livestream"
    return videos


def add_engagement_column(videos):
    """Same formula as main.py: (likes + comments) / views x 100."""
    # A video with 0 views would mean dividing by zero, so only videos that
    # have been watched are kept.
    videos = videos[videos["views"] > 0].copy()
    reactions = videos["likes"] + videos["comments"]
    videos["engagement"] = reactions / videos["views"] * 100
    return videos


def summarize(videos, group_columns):
    """Count, median views and median engagement for each group.

    Medians, not averages: one viral video can drag an average far away from
    what a typical video does, but it barely moves the median.
    """
    summary = videos.groupby(group_columns).agg(
        videos=("video_id", "count"),
        median_views=("views", "median"),
        median_engagement=("engagement", "median"),
    )
    # A median of an even-sized group can land between two videos (e.g.
    # 1500.5), so it is rounded and stored as a whole number of views.
    summary["median_views"] = summary["median_views"].round().astype(int)
    summary["median_engagement"] = summary["median_engagement"].round(2)
    return summary


def drop_small_groups(summary):
    """Remove groups with fewer than MIN_GROUP_SIZE videos, and say so."""
    big_enough = summary[summary["videos"] >= MIN_GROUP_SIZE]
    hidden = len(summary) - len(big_enough)
    if hidden > 0:
        print("(" + str(hidden) + " groups with fewer than " + str(MIN_GROUP_SIZE)
              + " videos hidden - too small to trust)")
    return big_enough


def growth_per_video(snapshots):
    """One row per video: views gained between its first and latest reading.

    This is momentum, measured across every reading collected so far rather
    than just the last two. Videos with only one reading are left out,
    because there is nothing to compare against yet.
    """
    # Oldest first, so "first" in the groupby below means the earliest
    # reading and "last" means the newest.
    oldest_first = snapshots.sort_values("recorded_at")

    growth = oldest_first.groupby("video_id").agg(
        channel_handle=("channel_handle", "last"),
        title=("title", "last"),
        published_at=("published_at", "last"),
        duration_seconds=("duration_seconds", "last"),
        is_live=("is_live", "last"),
        readings=("id", "count"),
        first_views=("views", "first"),
        last_views=("views", "last"),
        first_recorded=("recorded_at", "first"),
        last_recorded=("recorded_at", "last"),
    )
    growth = growth[growth["readings"] >= 2].copy()

    # The dates are stored as ISO text. pd.to_datetime turns them into real
    # dates so they can be subtracted, like datetime.fromisoformat in main.py.
    first_time = pd.to_datetime(growth["first_recorded"])
    last_time = pd.to_datetime(growth["last_recorded"])
    published = pd.to_datetime(growth["published_at"])

    growth["hours_tracked"] = (last_time - first_time).dt.total_seconds() / 3600
    growth = growth[growth["hours_tracked"] >= MIN_HOURS_BETWEEN].copy()

    growth["views_gained"] = growth["last_views"] - growth["first_views"]
    growth["momentum"] = growth["views_gained"] / growth["hours_tracked"]

    # How old the video already was when tracking started. New videos gain
    # views much faster than old ones, so age has to be shown alongside
    # momentum or the comparison is unfair.
    age_seconds = (first_time - published).dt.total_seconds()
    growth["age_days"] = age_seconds / 86400
    return growth


def summarize_growth(growth, group_columns):
    """Count, median momentum, and median age for each group."""
    summary = growth.groupby(group_columns).agg(
        videos=("momentum", "count"),
        median_views_per_hour=("momentum", "median"),
        median_age_days=("age_days", "median"),
    )
    summary["median_views_per_hour"] = summary["median_views_per_hour"].round().astype(int)
    summary["median_age_days"] = summary["median_age_days"].round().astype(int)
    summary = summary.sort_values("median_views_per_hour", ascending=False)
    return summary


def main():
    snapshots = load_snapshots()
    if snapshots is None:
        return

    videos = latest_reading_per_video(snapshots)
    videos = add_format_column(videos)
    videos = add_engagement_column(videos)

    channel_count = videos["channel_handle"].nunique()
    print("Loaded " + str(len(snapshots)) + " readings of " + str(len(videos))
          + " videos from " + str(channel_count) + " channels.")
    print("")

    print("=== Videos by length, all channels together ===")
    print(summarize(videos, ["format"]).to_string())
    print("")

    # The overall table can mislead: if one huge channel posts most of the
    # short videos, the "3 min or less" row mostly describes that channel.
    # Comparing length groups inside each channel removes that effect.
    print("=== The same comparison inside each channel ===")
    print(drop_small_groups(summarize(videos, ["channel_handle", "format"])).to_string())
    print("")

    growth = growth_per_video(snapshots)
    if len(growth) == 0:
        print("=== Growth over time ===")
        print("No video has two readings at least " + str(MIN_HOURS_BETWEEN)
              + " hour apart yet. Run main.py --all again later.")
        print("")
    else:
        growth = add_format_column(growth)
        median_hours = round(growth["hours_tracked"].median(), 1)
        print("=== Growth over time: views gained per hour while tracked ===")
        print(str(len(growth)) + " videos have 2+ readings, tracked for a median of "
              + str(median_hours) + " hours.")
        print("")
        print("By channel:")
        print(drop_small_groups(summarize_growth(growth, ["channel_handle"])).to_string())
        print("")
        print("By format:")
        print(drop_small_groups(summarize_growth(growth, ["format"])).to_string())
        print("")

    print("Length groups use each video's duration as YouTube reports it.")


if __name__ == "__main__":
    main()
