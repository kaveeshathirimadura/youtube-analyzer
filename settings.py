"""Settings shared by main.py and analyze.py.

Kept in its own file so each decision is made in exactly one place: both
scripts import from here instead of keeping their own copy.
"""

# Engagement rate, performance index ("Vs. average") and trend are new
# metrics calculated from YouTube's numbers, and so is ranking channels
# against each other by growth. YouTube's Developer Policies (section
# III.E.4.h) restrict creating new metrics from API data unless a developer
# has been approved through YouTube's analytics amendment.
#
# Until that approval exists, these stay switched off for real YouTube data.
# The code for them is kept, so approval would mean changing this one line
# to True - nothing would need to be rebuilt.
ADVANCED_METRICS = True
