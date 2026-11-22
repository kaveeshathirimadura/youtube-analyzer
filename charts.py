"""Draw charts from the readings that main.py has collected in history.db.

analyze.py prints tables. This file turns the same data into pictures.
It reuses analyze.py's functions instead of copying them, so the tables and
the charts can never disagree about how something is calculated.

Run it from the project folder:
    python charts.py
"""
import os
import math
import webbrowser
import pandas as pd
import matplotlib.pyplot as plt

from analyze import load_snapshots, growth_per_video

CHART_FILE = "growth_chart.png"

# How many videos to draw per channel. More than three lines in a panel this
# small turns into a tangle nobody can read.
VIDEOS_PER_CHANNEL = 3

# Panels per row. Nine channels makes a neat 3 x 3 grid.
PANELS_PER_ROW = 3

# Colors checked with a colorblind-safety tool: blue, orange, aqua.
# Each line also gets its own marker shape, so color is never the only clue.
LINE_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]
MARKER_SHAPES = ["o", "s", "^"]

BACKGROUND = "#fcfcfb"
TEXT_COLOR = "#2f2f2c"
GRID_COLOR = "#e4e3de"

# Titles longer than this are cut off so the legend fits inside the panel.
TITLE_LENGTH = 28


def short_title(title):
    """Shorten a video title and drop emoji so it fits in a legend.

    The chart font has no emoji, so they would print as empty boxes.
    Keeping only plain (ASCII) characters avoids that.
    """
    kept = ""
    for character in title:
        if character.isascii():
            kept = kept + character
    kept = kept.strip()

    if len(kept) > TITLE_LENGTH:
        kept = kept[:TITLE_LENGTH].strip() + "..."
    return kept


def format_views(value, position):
    """Turn an axis number like 1200000 into '1.2M' for the y-axis labels.

    matplotlib calls this for every tick label. It passes the tick's position
    too, which we don't need, but the function has to accept it.
    """
    if abs(value) >= 1000000:
        return str(round(value / 1000000, 1)) + "M"
    if abs(value) >= 1000:
        return str(round(value / 1000, 1)) + "K"
    return str(int(value))


def readings_since_start(snapshots, video_id):
    """Every reading of one video, as hours since its first reading and
    views gained since its first reading.

    Starting every line at (0, 0) means each line's steepness is its growth,
    so videos with very different total views can sit side by side.
    """
    readings = snapshots[snapshots["video_id"] == video_id]
    readings = readings.sort_values("recorded_at")

    times = pd.to_datetime(readings["recorded_at"])
    hours = (times - times.iloc[0]).dt.total_seconds() / 3600
    views_gained = readings["views"] - readings["views"].iloc[0]
    return hours, views_gained


def draw_channel_panel(axis, snapshots, channel_growth, handle):
    """Draw one channel's fastest-growing videos onto one panel."""
    top_videos = channel_growth.sort_values("views_gained", ascending=False)
    top_videos = top_videos.head(VIDEOS_PER_CHANNEL)

    line_number = 0
    for video_id, video in top_videos.iterrows():
        hours, views_gained = readings_since_start(snapshots, video_id)
        axis.plot(
            hours, views_gained,
            color=LINE_COLORS[line_number],
            marker=MARKER_SHAPES[line_number],
            markersize=6,
            linewidth=2,
            label=short_title(video["title"]),
        )
        line_number = line_number + 1

    axis.set_title(handle, loc="left", fontsize=11, color=TEXT_COLOR, fontweight="bold")
    axis.yaxis.set_major_formatter(format_views)
    axis.set_ylim(bottom=0)
    axis.set_xlim(left=0)

    # A quiet grid and no box around the panel, so the lines stand out.
    axis.grid(True, color=GRID_COLOR, linewidth=0.8)
    axis.set_axisbelow(True)
    for side in ["top", "right"]:
        axis.spines[side].set_visible(False)
    axis.tick_params(colors=TEXT_COLOR, labelsize=8)
    axis.legend(fontsize=7, loc="upper left", frameon=False)


def draw_growth_chart(snapshots, growth):
    """Draw one panel per channel and save the picture. Returns the file name."""
    handles = sorted(growth["channel_handle"].unique())

    # Enough rows to fit every channel. math.ceil rounds up, so 7 channels
    # at 3 per row gives 3 rows, not 2.
    rows = math.ceil(len(handles) / PANELS_PER_ROW)

    figure, axes = plt.subplots(
        rows, PANELS_PER_ROW,
        figsize=(15, 4 * rows),
        facecolor=BACKGROUND,
        layout="constrained",
        squeeze=False,
    )

    panel_number = 0
    for handle in handles:
        # Work out which row and column this panel goes in, without % or //:
        # every PANELS_PER_ROW panels, move down a row.
        row = int(panel_number / PANELS_PER_ROW)
        column = panel_number - row * PANELS_PER_ROW
        axis = axes[row][column]
        axis.set_facecolor(BACKGROUND)

        channel_growth = growth[growth["channel_handle"] == handle]
        draw_channel_panel(axis, snapshots, channel_growth, handle)
        panel_number = panel_number + 1

    # Hide any leftover empty panels in the last row.
    while panel_number < rows * PANELS_PER_ROW:
        row = int(panel_number / PANELS_PER_ROW)
        column = panel_number - row * PANELS_PER_ROW
        axes[row][column].set_visible(False)
        panel_number = panel_number + 1

    figure.suptitle(
        "Views gained since tracking started - fastest 3 videos per channel",
        fontsize=14, color=TEXT_COLOR, x=0.01, ha="left",
    )
    figure.supxlabel("Hours since first reading", color=TEXT_COLOR)
    figure.supylabel("Views gained (each panel has its own scale)", color=TEXT_COLOR)

    figure.savefig(CHART_FILE, dpi=120, facecolor=BACKGROUND)
    plt.close(figure)
    return CHART_FILE


def main():
    snapshots = load_snapshots()
    if snapshots is None:
        return

    growth = growth_per_video(snapshots)
    if len(growth) == 0:
        print("Nothing to chart yet: no video has two readings at least an hour apart.")
        print("Let the daily collection run again, then try this.")
        return

    chart_path = draw_growth_chart(snapshots, growth)
    print("Saved " + chart_path + " (" + str(len(growth)) + " videos with growth data).")
    webbrowser.open("file://" + os.path.abspath(chart_path))


if __name__ == "__main__":
    main()
