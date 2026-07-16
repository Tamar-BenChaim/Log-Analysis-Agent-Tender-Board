"""
Fetch Tender Board Activity Logs (originally Story SCRUM-37).

This module owns exactly one responsibility: getting raw log records
out of MongoDB and into plain Python data structures (list[dict]).

It intentionally does NOT classify, count, or summarize anything - see
agent.nodes.classify / agent.nodes.report. Keeping this file narrow
means it can be wired directly as a LangGraph node (agent.graph) with
no rewriting.

Where the data actually lives
------------------------------
There is no dedicated "tender board only" log collection. All log lines
from the whole Node/Express backend (server startup, DB connection,
every route, every module) are written by winston into one shared
collection: `test.applicationlogs`. Each document looks like:

    {
        "level": "info",
        "message": "Tender created successfully",   # or "GET /tender-board 200 159ms", etc.
        "timestamp": datetime(...),
        "createdAt": datetime(...),
        "updatedAt": datetime(...),
        "expiresAt": datetime(...),
        "__v": 0,
    }

So "fetch Tender Board activity logs" = fetch documents from that shared
collection whose `message` mentions "tender" (case-insensitive). This
catches both the explicit logger.info(...) calls inside
tenderBoardService.ts / tenderBoardAIService.ts (e.g. "Tender created
successfully", "Applicant registered successfully to tender") AND the
HTTP access-log lines for the /tender-board/* routes (e.g.
"GET /tender-board/product-types 200 4ms").
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

DEFAULT_DATE_FIELD = "timestamp"
DEFAULT_MESSAGE_FIELD = "message"
DEFAULT_TENDER_KEYWORD = "tender"


def get_mongo_client(uri: Optional[str] = None) -> MongoClient:
    """
    Create (and verify) a MongoDB client connection.

    Parameters
    ----------
    uri:
        Optional connection string override. If not given, it is read
        from the MONGODB_URI environment variable (loaded from .env).

    Raises
    ------
    RuntimeError
        If the URI is missing, or if the server does not respond to a
        ping within the connection timeout - i.e. "connect successfully
        to the data source" from the Definition of Done failed.
    """
    resolved_uri = uri or os.environ.get("MONGODB_URI")
    if not resolved_uri:
        raise RuntimeError(
            "MONGODB_URI is not set. Add it to your .env file, e.g.:\n"
            "MONGODB_URI=mongodb+srv://<user>:<password>@<cluster-url>/"
        )

    # serverSelectionTimeoutMS keeps a bad/unreachable URI from hanging
    # the whole process for the default 30s - fail fast instead.
    client: MongoClient = MongoClient(resolved_uri, serverSelectionTimeoutMS=5000)

    try:
        # The client above is "lazy" - it does not actually open a socket
        # until you ask it to do something. `ping` is the cheapest possible
        # round trip, used here purely to confirm the connection works.
        client.admin.command("ping")
    except PyMongoError as exc:
        raise RuntimeError(f"Could not connect to MongoDB: {exc}") from exc

    return client


def get_application_logs_collection(client: Optional[MongoClient] = None) -> Collection:
    """
    Resolve the Collection object that holds the shared application logs
    (test.applicationlogs by default - see module docstring for why).

    `client` can be injected - production code leaves it out and a real
    MongoClient is built from environment variables; tests pass in a
    mongomock client instead, so no real database is ever touched.
    """
    if client is None:
        client = get_mongo_client()

    db_name = os.environ.get("MONGODB_DB_NAME")
    collection_name = os.environ.get("MONGODB_COLLECTION_NAME")

    if not db_name or not collection_name:
        raise RuntimeError(
            "MONGODB_DB_NAME and MONGODB_COLLECTION_NAME must be set in .env"
        )

    return client[db_name][collection_name]


def fetch_tender_board_activity_logs(
    start_date: datetime,
    end_date: datetime,
    collection: Optional[Collection] = None,
    date_field: str = DEFAULT_DATE_FIELD,
    message_field: str = DEFAULT_MESSAGE_FIELD,
    tender_keyword: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Fetch Tender Board-related log records for a date range.

    The underlying collection is a shared, whole-application log, so this
    function filters down to rows whose `message_field` contains
    `tender_keyword` (case-insensitive) in addition to the date range.

    Parameters
    ----------
    start_date, end_date:
        Inclusive boundaries of the period to fetch (datetime objects).
    collection:
        Optional Collection override, used by tests to inject a
        mongomock collection instead of a real one.
    date_field:
        Name of the field storing the event timestamp. Defaults to
        "timestamp".
    message_field:
        Name of the field storing the free-text log message. Defaults
        to "message".
    tender_keyword:
        Case-insensitive substring that identifies a log line as
        tender-board-related. Defaults to the MONGODB_TENDER_KEYWORD
        env var, falling back to "tender".

    Returns
    -------
    A list of plain dicts - one per log record. An empty list is a
    valid, normal result (no records in range) and is returned instead
    of raising, per the Definition of Done: "handles the case of no
    records (ends in a controlled manner)".
    """
    if collection is None:
        collection = get_application_logs_collection()

    keyword = tender_keyword or os.environ.get(
        "MONGODB_TENDER_KEYWORD", DEFAULT_TENDER_KEYWORD
    )

    query = {
        date_field: {"$gte": start_date, "$lte": end_date},
        message_field: {"$regex": keyword, "$options": "i"},
    }

    try:
        cursor = collection.find(query)
        records = list(cursor)
    except PyMongoError as exc:
        # Wrapped so callers only need to catch one exception type,
        # regardless of which underlying pymongo error occurred.
        raise RuntimeError(f"Failed to fetch tender board activity logs: {exc}") from exc

    return records


if __name__ == "__main__":
    # Manual smoke-test entry point for local development only.
    # The real CLI (with formatted output) lives in agent/cli.py.
    from dotenv import load_dotenv

    load_dotenv()

    _client = get_mongo_client()
    _collection = get_application_logs_collection(_client)
    _records = fetch_tender_board_activity_logs(
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 12, 31),
        collection=_collection,
    )
    print(f"Fetched {len(_records)} tender board record(s).")
