"""
TikTok Conflict Study - data collection package.

Package layout
--------------
    config.py         Loads and validates every setting from the .env file.
    logging_setup.py  Configures file + console logging for a run.
    models.py         The Post and Comment data classes, and the CSV schemas.
    storage.py        Append-safe, resumable CSV writers.
    downloader.py     Downloads .mp4 files, named by post_id.
    transfer.py       Copies the finished dataset to the college server.
    pipeline.py       Orchestrates all of the above into one collection run.
    clients/          The data sources (mock and live Research API).

Entry point
-----------
    run_collection.py in the project root. Run `python run_collection.py --help`.
"""

__version__ = "1.0.0"
