"""Entity resolution: canonical Person/Org IDs from many fragmented sources.

The blueprint calls this the central hard problem. Heavyweight matchers
(Splink probabilistic linkage, a fine-tuned bi-encoder) plug in on top of
the deterministic comparison substrate defined here.
"""
