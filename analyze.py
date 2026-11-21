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
    """Label each video as a Short, a Livestream, or Long-form."""
    # Start by calling everything Long-form, then overwrite the rows that are
    # something else. .loc[condition, column] means "these rows, this column".
    videos["format"] = "Long-form"
    videos.loc[videos["is_short"] == 1, "format"] = "Short"

    # Livestreams are checked last so they win: a stream is its own thing,
    # whatever its length.
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

    print("=== Shorts vs long-form, all channels together ===")
    print(summarize(videos, ["format"]).to_string())
    print("")

    # The overall table can mislead: if one huge channel posts most of the
    # Shorts, the "Short" row mostly describes that channel. Comparing formats
    # inside each channel removes that effect.
    print("=== The same comparison inside each channel ===")
    print(summarize(videos, ["channel_handle", "format"]).to_string())
    print("")

    print("Note: YouTube has no 'is a Short' field. A video counts as a Short")
    print("here if it is 3 minutes or less, so short regular videos are mislabelled.")


if __name__ == "__main__":
    main()
