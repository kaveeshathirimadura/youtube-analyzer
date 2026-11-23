// Sorting for the video rows in report.html.
//
// main.py writes each video's raw numbers into its row as data- attributes,
// for example <article class="video" data-views="1534221" ...>. Sorting only
// reads those numbers and moves the rows around. Nothing is fetched.

// Which data- attribute we are sorting by right now. compareRows() reads it.
let sortKey = "position";

function readNumber(row) {
    // row.dataset.views reads the data-views attribute. It comes back as
    // text, so Number() turns it into a number that can be compared.
    const text = row.dataset[sortKey];

    // Momentum is blank until the program has run twice. -Infinity is smaller
    // than every real number, so blank rows always sink to the bottom.
    if (text === "") {
        return -Infinity;
    }
    return Number(text);
}

function compareRows(rowA, rowB) {
    // sort() calls this with two rows at a time. A negative result puts rowA
    // first, a positive result puts rowB first, and 0 keeps their order.
    const a = readNumber(rowA);
    const b = readNumber(rowB);

    // Biggest first: most views, highest engagement, and so on.
    let result = 0;
    if (a > b) {
        result = -1;
    }
    if (a < b) {
        result = 1;
    }

    // Except "Newest": position 0 is the newest video, so smallest first.
    if (sortKey === "position") {
        result = -result;
    }
    return result;
}

function sortVideos(key) {
    sortKey = key;

    const list = document.getElementById("video-list");

    // querySelectorAll gives a NodeList, which has no sort(), so the rows are
    // copied into a normal array first.
    const rows = Array.from(list.querySelectorAll(".video"));
    rows.sort(compareRows);

    // appendChild on a row that is already in the list moves it to the end.
    // Moving every row to the end in sorted order leaves them sorted.
    for (let i = 0; i < rows.length; i++) {
        list.appendChild(rows[i]);
    }

    // Mark which button is on. aria-pressed tells screen readers, and
    // style.css uses the same attribute to fill the button in, so there is
    // only one thing to keep up to date.
    const buttons = document.querySelectorAll(".sort-button");
    for (let i = 0; i < buttons.length; i++) {
        if (buttons[i].dataset.sort === key) {
            buttons[i].setAttribute("aria-pressed", "true");
        } else {
            buttons[i].setAttribute("aria-pressed", "false");
        }
    }
}
