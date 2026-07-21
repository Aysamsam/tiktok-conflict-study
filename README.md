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
├── downloads/
│   └── videos/
│       ├── 7382910473829104738.mp4     ← named by post_id
│       └── ...
│
└── logs/
    └── collection_20260721_141530.log  Full provenance record of each run
```

Video files are named by `post_id` and nothing else, so any metadata row links
to its video by a single string operation with no lookup table.

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
| `video_path` | String | Project-relative path to the `.mp4`; blank if not downloaded |
| `view_count` | Integer | Views at collection time |
| `like_count` | Integer | Likes at collection time |
| `comment_count` | Integer | Comment count reported by TikTok |
| `share_count` | Integer | Shares at collection time |
| `favorites_count` | Integer | Times saved to favourites |
| `collected_at` | Timestamp | When this row was written, ISO 8601 UTC |

**On `ai_label_kind` — important for analysis.** The Research API reports
content labels in a `video_tag` field, and it distinguishes two *different*
AI labels that this pipeline keeps separate:

| `video_tag` | `ai_label_kind` | Meaning |
|---|---|---|
| `{type: AIGC, number: 1}` | `creator_labelled` | **Self-disclosed.** The creator voluntarily flipped the AI-disclosure toggle. |
| `{type: AIGC, number: 2}` | `ai_generated` | **Platform-detected.** Applied by TikTok, typically from C2PA content credentials embedded by the generating tool. |

These answer different research questions — one measures creator behaviour, the
other measures provenance metadata that survived upload. Collapsing them into a
single boolean at collection time would destroy the distinction permanently, so
both are recorded. `has_ai_label` is `1` for either.

`{type: Branded, ...}` tags are commercial disclosures (Paid Partnership,
Creator Commission) and are explicitly **not** counted as AI labels.

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

Three schema decisions worth noting in a methods section:

- **IDs are strings, never integers.** TikTok IDs are 19 digits. Spreadsheet
  software silently rounds integers that large, which would corrupt the key
  joining posts, comments and video files. When opening these files in Excel,
  import `post_id` and `comment_id` as *Text*.
- **`has_ai_label` is `1`/`0`, not `True`/`False`.** Numeric flags import
  cleanly into R and SPSS; the strings are read as text and need recoding.
- **Engagement metrics and `has_ai_label` are point-in-time snapshots**, which
  is why `collected_at` appears on every row.

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
            ├── src/downloader.py   .mp4 retrieval, named by post_id
            └── src/transfer.py     Delivery to the college server
```

The organising idea is that **`pipeline.py` contains the study's procedure and
nothing else** — no HTTP code, no CSV escaping, no file I/O details. A reviewer
can read that one file and understand exactly what the study did.

`clients/base.py` defines an interface that both data sources implement. The
pipeline never knows which one it has. That is what makes the switch from mock
to live API a single line in `.env`.

---

## 5. Switching from mock to the real API

The mock exists so the pipeline can be built, reviewed and demonstrated before
the institutional Research API application is approved. When credentials
arrive:

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
hours. The client caches it and refreshes 60 seconds before expiry. The
approved scopes (`research.data.basic`, `research.data.o18`) are granted by the
approval itself — the client-credentials flow sends no scope parameter.

The live client handles the constraints that make this API awkward: the
**30-day maximum query window** (the 4-month study period is split into legal
chunks automatically, with no gaps or overlaps), **stateful cursor pagination**
(`cursor` and `search_id` must be echoed back together), **rate limiting**
(politeness delay, exponential backoff on 429, and a daily quota ceiling), and
**application-level errors returned inside HTTP 200 responses**.

---

## 6. Moving to the college server

Every path in the codebase derives from one setting, `OUTPUT_ROOT`. There are
two supported approaches:

**Preferred — write directly to the server.** If the research share is mounted,
point `OUTPUT_ROOT` at it and no copy step exists at all:

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
`SERVER_USER` and `SERVER_SSH_KEY_PATH`. Key-based authentication only — no
password is ever read from configuration, and host-key verification is left at
its strict default.

Transfer runs **after** collection completes and all files are closed, so a
failed upload never damages a completed dataset; the local copy stays intact
and the transfer can simply be retried.

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
  --no-videos                    Metadata and comments only
  --log-level {DEBUG,...}        DEBUG shows every pipeline decision
  --dry-run                      Validate config and exit
```

`.env` holds the study's standing configuration; these flags allow a one-off
deviation without editing it, so a temporary experiment cannot silently become
the permanent configuration. Every override is written to the log, so each log
records the parameters actually used.

Exit codes: `0` success · `1` configuration error · `2` collection error.

---

## 8. Reliability properties

The collector is designed on the assumption that runs get interrupted.

- **Every row is flushed to disk as it is collected.** An interrupted run
  leaves a valid, complete CSV of everything gathered so far.
- **Re-running resumes; it never duplicates.** Existing IDs are loaded at
  start-up and skipped *before* any expensive work — no re-downloading, no
  re-fetching of comment threads.
- **Posts are written before their comments**, so an interrupted run can never
  leave orphan comments pointing at a post that was never saved.
- **Videos download to a `.part` file** and are renamed atomically only when
  complete, so a file named `<post_id>.mp4` is always whole.
- **One bad post never ends a run.** A deleted post or a locked comment thread
  is logged, counted, and stepped over.
- **Ctrl-C is safe** and the next run picks up exactly where it stopped.

Each run ends with a summary stating posts seen, saved, skipped as duplicates,
skipped as ineligible, comments saved, videos downloaded and non-fatal errors —
the numbers a methods section needs.

---

## 9. Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

53 tests, offline, under a second. They assert the properties the study's
validity depends on: schema conformance, no duplicate posts or comments,
referential integrity between the two files, comments never predating their
post, correct inclusion/exclusion at the date-window boundaries, resumability,
the `video_tag` → `ai_label_kind` mapping, and that captions containing commas,
quotes, newlines, emoji and Arabic, Hebrew and Persian text survive the round
trip unchanged.

Several tests document real bugs found during development and guard against
their return:

- `test_mock_client_is_deterministic` — the mock's seed came from Python's
  `hash()`, which is randomised per process, so every run produced different
  IDs and deduplication could never engage.
- `test_comments_never_predate_their_post` — comment timestamps were generated
  independently of the post's own.
- `test_language_is_not_a_queryable_field` — the client sent a `video_language`
  filter condition the endpoint does not accept, which would have returned an
  opaque HTTP 400 on *every* request.
- `test_branded_tag_is_not_an_ai_label` — Paid Partnership disclosures share the
  `video_tag` field with AI labels and must not inflate the headline number.
- `test_target_sample_size_is_reached_despite_language_filtering` — local
  filtering silently shrank the sample below the configured target.
- `test_second_run_adds_nothing` — the sample-size target counted only *new*
  rows, so each re-run appended another full sample and inflated the study's n.

---

## 10. Known limitations — please read before collecting

**Video downloads are disabled by default, deliberately.** The TikTok Research
API returns metadata only. It does not provide a direct `.mp4` link, and
fetching from TikTok's media CDN is restricted by their Terms of Service. The
pipeline therefore records posts with an empty `video_path` rather than
attempting an unauthorised retrieval.

This is an ethical default, not an unfinished feature. Once your institution
confirms which media-access route its agreement permits, implement
`resolve_video_download_url()` in `src/clients/research_client.py` — one method,
in one file — and downloading begins working with no other changes. Everything
downstream is already built and tested.

**Verify `has_ai_label` against one known post before the full run.** The
`video_tag` mapping above is taken from TikTok's published documentation and is
covered by eleven unit tests, but it has not been exercised against a live
response. Before collecting, fetch one post you know carries the label, print
the raw JSON, and confirm the field appears as documented for your account.
Getting this wrong would record every post as not-AI-labelled — a systematic
error that is nearly invisible in a finished dataset because nothing looks
broken.

**The label measures disclosure, not synthesis.** Its absence is not evidence
that a video is authentic, and it can be added or removed after publication —
which is why `collected_at` is recorded on every row.

**Language filtering happens locally, and this has costs.** The Research API's
video-query endpoint accepts filter conditions on only eleven fields
(`create_date`, `username`, `region_code`, `video_id`, `hashtag_name`,
`keyword`, `music_id`, `effect_id`, `video_length`, `view_count`,
`comment_count`) — **none of which is language**. So English-only filtering is
applied to caption text after retrieval, which means:

- *Quota cost.* Non-English posts are fetched and then discarded.
  `SEARCH_OVERFETCH_FACTOR` (default 3) compensates so the target sample size is
  still reached; budget roughly that multiple of your sample in API records.
- *Accuracy limits.* Statistical language ID is unreliable on short text, and
  captions are short. The pipeline strips hashtags, mentions, URLs and emoji
  first, and refuses to classify text under 20 characters — keeping such posts
  by default (`keep_undetermined`), on the reasoning that a post discarded at
  collection time can never be recovered, while one collected in error can
  always be filtered during analysis. Report the detector and threshold in your
  methods section.

Filtering by `region_code` was considered as a server-side proxy and
**rejected**: it would systematically exclude English-language posts originating
from Iran and Israel, plausibly the most analytically important cases in this
study. See [src/language.py](src/language.py) for the full reasoning.

**API quota is 1000 requests/day**, resetting at 00:00 UTC, for up to ~100,000
records. The client counts its own requests, warns at 80%, and stops cleanly at
the limit rather than running into a wall of HTTP 429s — everything collected is
saved and the next run resumes where it stopped. Note that comment collection
dominates: each post costs one search request *plus* one comment request per 100
comments, so a 200-comment cap means up to 2 extra requests per post.

**Your application expires 30 November 2026.** All collection must complete
before then. The study window ends 31 July 2026, leaving four months of margin,
but plan any follow-up waves around that date.

**Mock data is fictional.** Every synthetic post carries `MOCK_` in its
username and each run logs a warning. It must never be mixed with, or reported
as, collected data.

---

## 11. Ethics and data protection

This dataset contains personal data: usernames, and user-authored caption and
comment text on a politically sensitive topic involving an active conflict.

- `.gitignore` excludes `data/`, `downloads/` and `logs/` from version control.
- Store the dataset only on approved institutional storage.
- Secure ethics approval before collection, and follow your institution's
  guidance on pseudonymisation before any publication or data sharing.
- The `.env` file holding API credentials is git-ignored and must never be
  committed or emailed.
