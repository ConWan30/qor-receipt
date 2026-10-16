"""AI referee for QorCap match bundles: ``qortime referee-ai <bundle>``.

Three layers, each with one job (see docs/REFEREE_AI.md):

* seeing (``vision``): pluggable frame reader. Default = QorTime's own scorebug reader; ``meta`` is a
  documented stub for Meta Muse Spark (no calls until a key exists).
* judgment (``jev``): TypeSafe Jev System One with image input. Frame type + score cross-check. A field
  is accepted only at confidence >= 0.9 AND agreement across 2-3 frames; otherwise Jev abstains.
* integrity (``integrity``): plain code. Hash chains, signatures, legal score steps, monotonic clock,
  final screen == last score. Tampering is caught here, never by a model.

The output (``verdict``) is a signed ``qortime-referee-verdict/v0`` JSON written next to the bundle.
"""
