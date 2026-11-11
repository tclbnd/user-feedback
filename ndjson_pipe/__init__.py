"""Resumable streaming NDJSON collection and ingestion pipeline.

Modules:
    decoder   -- byte-oriented streaming NDJSON line splitter (UTF-8 safe)
    normalize -- versioned record normalization with exact decimal amounts
    store     -- SQLite storage with record / rejection / conflict / checkpoint
    importer  -- resumable pipeline orchestration with crash injection points
    reference -- independent batch reference implementation for differential tests
    cli       -- command line entry point
"""

__version__ = "1.0.0"
