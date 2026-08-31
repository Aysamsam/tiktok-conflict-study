# TikTok Conflict Study — Data Collection Pipeline

A documented, reproducible collector for TikTok posts and comments relating to
the Israel–Iran conflict, built for academic research.

**Study window:** 1 April 2026 – 31 July 2026 · **Language:** English ·
**Hashtags:** `#IsraelIran`, `#IsraelIranWar`, `#Iranwar`, `#Iran`

---

## 1. Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Create your configuration file from the template
cp .env.example .env          # Windows: copy .env.example .env

# 3. Validate the configuration without collecting anything
python run_collection.py --dry-run

# 4. Run a 20-post pilot using the offline mock data source
python run_collection.py
```

No credentials are needed for steps 1–4. The default configuration uses the
**mock** data source, which generates realistic synthetic data offline.

---

## 2. What gets produced

```
tiktok_conflict_study/
│
├── data/
│   ├── raw_posts.csv          Master spreadsheet of video metadata
│   └── raw_comments.csv       Master spreadsheet of all comments
│
└── logs/
    └── collection_20260721_141530.log  Full provenance record of each run
```

Video files themselves are not collected - see section 10. Each post's
`video_url` column is the citable reference to the original.

---

## 3. Schema

### `raw_posts.csv`

| Column | Type | Description |
|---|---|---|
| `post_id` | String | Unique TikTok post identifier (19 digits, **stored as text**) |
| `username` | String | Poster's handle, without `@` |
| `author_region` | String | ISO country code; blank when unavailable |
| `create_time` | Timestamp | Publication time, ISO 8601 UTC |
| `caption` | Text | Full video caption, verbatim |
| `hashtags` | String | Comma-separated, without `#` |
| `has_ai_label` | Integer | `1` if TikTok applied any AI-content label, else `0` |
| `ai_label_kind` | String | `creator_labelled` \| `ai_generated` \| `""` — see below |
| `video_url` | String | Canonical public permalink |
| `view_count` | Integer | Views at collection time |
| `like_count` | Integer | Likes at collection time |
| `comment_count` | Integer | Comment count reported by TikTok |
| `share_count` | Integer | Shares at collection time |
| `favorites_count` | Integer | Times saved to favourites |
| `collected_at` | Timestamp | When this row was written, ISO 8601 UTC |

**On `ai_label_kind`.** The Research API reports content labels in a
`video_tag` field, distinguishing two AI labels that this pipeline keeps
separate:

| `video_tag` | `ai_label_kind` | Meaning |
|---|---|---|
| `{type: AIGC, number: 1}` | `creator_labelled` | **Self-disclosed.** The creator voluntarily flipped the AI-disclosure toggle. |
| `{type: AIGC, number: 2}` | `ai_generated` | **Platform-detected.** Applied by TikTok, typically from C2PA content credentials embedded by the generating tool. |

These answer different research questions - one measures creator behaviour,
the other measures provenance metadata - so both are recorded rather than
collapsed into a single boolean. `has_ai_label` is `1` for either.

`{type: Branded, ...}` tags are commercial disclosures (Paid Partnership,
Creator Commission) and are **not** counted as AI labels.

### `raw_comments.csv`

| Column | Type | Description |
|---|---|---|
| `comment_id` | String | Unique comment identifier |
| `post_id` | String | Foreign key into `raw_posts.csv` |
| `comment_text` | Text | Comment body, verbatim |
| `like_count` | Integer | Likes on this comment |
| `reply_count` | Integer | Replies this comment received |
| `create_time` | Timestamp | When the comment was posted, ISO 8601 UTC |
| `parent_comment_id` | String | Replied-to comment; blank if top-level |
| `collected_at` | Timestamp | When this row was written, ISO 8601 UTC |

Notes for a methods section:

- **IDs are strings, never integers.** TikTok IDs are 19 digits; spreadsheet
  software silently rounds integers that large. Import `post_id` and
  `comment_id` as *Text* when opening these files in Excel.
- **`has_ai_label` is `1`/`0`, not `True`/`False`**, so it imports cleanly
  into R and SPSS.
- **Engagement metrics and `has_ai_label` are point-in-time snapshots**,
  which is why `collected_at` appears on every row.

---

## 4. Architecture

```
run_collection.py          CLI entry point — argument parsing, exit codes
    │
    └── src/pipeline.py    THE PROCEDURE: what the study does, in order
            │
            ├── src/config.py       All settings, validated at start-up
            ├── src/clients/        WHERE data comes from
            │     ├── base.py           the interface both sources satisfy
            │     ├── mock_client.py    offline synthetic data
            │     └── research_client.py  live TikTok Research API
            ├── src/storage.py      Append-safe, resumable CSV writing
            └── src/transfer.py     Delivery to the college server
```

`pipeline.py` contains the study's procedure and nothing else - no HTTP code,
no CSV escaping, no file I/O details. `clients/base.py` defines an interface
both data sources implement, so the pipeline never knows which one it has -
that's what makes the switch from mock to live API a single line in `.env`.

---

## 5. Switching from mock to the real API

```diff
# in .env
- TIKTOK_CLIENT=mock
+ TIKTOK_CLIENT=research_api
  TIKTOK_CLIENT_KEY=your_real_key
  TIKTOK_CLIENT_SECRET=your_real_secret
```

No code changes.

**Which credential goes where.** The developer portal shows *three* values and
only two are used for authentication:

| Portal field | Used for |
|---|---|
| Client ID (e.g. `1780486653`) | Informational only — recorded in `.env` for provenance |
| **Client key** | `TIKTOK_CLIENT_KEY` — used by OAuth |
| **Client secret** | `TIKTOK_CLIENT_SECRET` — used by OAuth |

Passing the Client ID where the Client key belongs is a common cause of an
unexplained HTTP 401 on the first request.

Authentication uses the OAuth 2.0 *client credentials* grant against
`https://open.tiktokapis.com/v2/oauth/token/`, returning a token valid for two
hours; the client caches it and refreshes 60 seconds before expiry.

The live client handles: the **30-day maximum query window** (the 4-month
study period is split into legal chunks automatically), **stateful cursor
pagination** (`cursor` and `search_id` echoed back together), **rate
limiting** (politeness delay, exponential backoff on 429, daily quota
ceiling), and **application-level errors returned inside HTTP 200
responses**.

---

## 6. Moving to the server

Every path in the codebase derives from one setting, `OUTPUT_ROOT`.

**Preferred — write directly to the server.** If the research share is
mounted, point `OUTPUT_ROOT` at it and no copy step exists at all:

```bash
OUTPUT_ROOT=/mnt/research/tiktok_conflict_study
SERVER_TRANSFER_MODE=none
```

**Alternative — collect locally, then upload.** Keeps a local working copy:

```bash
OUTPUT_ROOT=.
SERVER_TRANSFER_MODE=copy        # or "sftp" for upload over SSH
SERVER_DEST_PATH=/mnt/research/tiktok_conflict_study
```

`sftp` mode additionally needs `pip install paramiko`, plus `SERVER_HOST`,
`SERVER_USER` and `SERVER_SSH_KEY_PATH`. Key-based authentication only.

Transfer runs **after** collection completes and all files are closed, so a
failed upload never damages a completed dataset - it can simply be retried.

---

## 7. Command-line reference

```
python run_collection.py [OPTIONS]

  --client {mock,research_api}   Override the data source
  --max-posts N                  Override the sample size
  --max-comments N               Override the per-post comment cap
  --start-date YYYY-MM-DD        Override the window start
  --end-date YYYY-MM-DD          Override the window end
  --hashtags TAG,TAG             Override the hashtag set
  --output-root PATH             Override where data is written
  --log-level {DEBUG,...}        DEBUG shows every pipeline decision
  --dry-run                      Validate config and exit
```

`.env` holds the study's standing configuration; these flags allow a one-off
deviation without editing it. Every override is written to the log.

Exit codes: `0` success · `1` configuration error · `2` collection error.

---

## 8. Reliability properties

- **Every row is flushed to disk as it is collected**, so an interrupted run
  leaves a valid, complete CSV.
- **Re-running resumes; it never duplicates.** Existing IDs are loaded at
  start-up and skipped before any expensive work.
- **Posts are written before their comments**, so no orphan comments.
- **One bad post never ends a run** - it is logged, counted, and stepped over.
- **Ctrl-C is safe**; the next run picks up where it stopped.

Each run ends with a summary of posts seen, saved, skipped (duplicate and
ineligible), comments saved and non-fatal errors.

---

## 9. Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

Offline, under a second. They assert schema conformance, no duplicate posts
or comments, referential integrity between the two files, comments never
predating their post, correct inclusion/exclusion at date-window boundaries,
resumability, the `video_tag` → `ai_label_kind` mapping, and that captions
containing commas, quotes, newlines, emoji and Arabic/Hebrew/Persian text
survive the round trip unchanged.

---

## 10. Known limitations — please read before collecting

**Video files are not collected, and this is not a missing feature.** The
Research API returns metadata only - no direct `.mp4` link - and TikTok's
Research Tools Terms of Service explicitly prohibit obtaining the content any
other way (no scraping, and no manual extraction outside the Research Tools
either, even by hand). This pipeline does not attempt video downloads at all.
Each post's `video_url` is the citable reference to the original; note it can
go dead if the video is later removed, which is why `collected_at` is
recorded on every row. If video-level analysis is genuinely required, that
needs either a separately licensed data source or a media-access provision
negotiated as part of your institution's own Research API agreement - neither
is something this pipeline can provide.

**Verify `has_ai_label` against one known post before the full run.** The
`video_tag` mapping above is taken from TikTok's published documentation and
covered by unit tests, but has not been exercised against a live response.
Fetch one post known to carry the label, print the raw JSON, and confirm the
field matches before collecting - getting this wrong would record every post
as not-AI-labelled, an error that looks like nothing is broken.

**The label measures disclosure, not synthesis.** Its absence is not evidence
that a video is authentic, and it can be added or removed after publication -
hence `collected_at` on every row.

**Language filtering happens locally**, because the Research API's
video-query endpoint has no language field among its eleven queryable fields.
English-only filtering is applied to caption text after retrieval, which
means:

- *Quota cost.* Non-English posts are fetched and then discarded.
  `SEARCH_OVERFETCH_FACTOR` (default 3) compensates so the target sample size
  is still reached.
- *Accuracy limits.* Statistical language ID is unreliable on short text.
  The pipeline strips hashtags, mentions, URLs and emoji first, and refuses
  to classify text under 20 characters, keeping such posts by default
  (`keep_undetermined`). Report the detector and threshold in your methods
  section.

Filtering by `region_code` was considered as a server-side proxy and
rejected: it would systematically exclude English-language posts from Iran
and Israel. See [src/language.py](src/language.py) for the full reasoning.

**API quota is 1000 requests/day**, resetting at 00:00 UTC, for up to
~100,000 records. The client counts its own requests, warns at 80%, and stops
cleanly at the limit - everything collected is saved and the next run resumes
where it stopped. Comment collection dominates: each post costs one search
request plus one comment request per 100 comments.

**Your application expires 30 November 2026.** All collection must complete
before then. The study window ends 31 July 2026, leaving margin for follow-up
waves.

**Mock data is fictional.** Every synthetic post carries `MOCK_` in its
username and each run logs a warning. It must never be mixed with, or
reported as, collected data.

---

## 11. Ethics and data protection

- Store the dataset only on approved institutional storage.
- Secure ethics approval before collection, and follow your institution's
  guidance on pseudonymisation before any publication or data sharing.
- The `.env` file holding API credentials is git-ignored and must never be
  committed or emailed.
