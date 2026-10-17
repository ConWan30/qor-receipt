"""Official post-hoc bundle review ("verified uploads").

A QorCap bundle recorded on the player's laptop is re-checked by the QorTime referee service, which holds the
official key. Nothing the laptop computed is trusted: hash chains are recomputed, video bytes are hashed against
the committed manifest (all of them, or a randomly sampled subset drawn *after* the manifest is committed),
the final score is re-read from the referee's own decode of the recorded end-of-game frames, challenge patterns
are re-run from the raw controller log, and only then is a receipt countersigned.

See docs/REFEREE_SERVICE.md for the trust model and what this does NOT prove.
"""
from qor_receipt.review.policy import (  # noqa: F401
    ANCHOR_TYPE,
    CHALLENGE_TOKEN_TYPE,
    REVIEW_KEY_KIND,
    REVIEW_TYPE,
)
