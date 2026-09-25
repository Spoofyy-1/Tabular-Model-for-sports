# Diagnostic only: empty NFL play metrics

Release `matchup-36179258148-1` completed its workflow but failed the subsequent coverage review: **zero team games had observed play counts**. Its pregame style columns therefore do not contain usable play histories. Do not use this release for team-style modeling or evaluation.

The pinned nflverse source stores `no_play` as a value of `play_type` and supplies no standalone `no_play` indicator. The initial collector incorrectly required that absent indicator to equal zero. It consequently excluded every play. This issue affects the NFL team-style release above; it does not affect the original NFL PBP archive, the published NBA matchup archive, or the injury and staff collectors.

The correction uses documented explicit play types, retains separate kneel/spike checks and adds a minimum positive-coverage check before publication. A corrected later NFL release must report positive observed-play coverage and usable historical feature counts. Preserve this release only as an audit of the failed collection. Its separate schedule/coach fields do not make its empty style features valid.
