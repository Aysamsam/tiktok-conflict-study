"""
Language detection for the English-only inclusion criterion.

WHY THIS MODULE HAS TO EXIST
----------------------------
The study design restricts the sample to English-language posts. The obvious
way to implement that is to ask the API for English posts only - but the TikTok
Research API's video-query endpoint accepts filter conditions on just eleven
fields (create_date, username, region_code, video_id, hashtag_name, keyword,
music_id, effect_id, video_length, view_count, comment_count), and none of them
describes language. There is no server-side language filter to use.

Two alternatives were considered and rejected:

  * Filter by `region_code` to English-speaking countries. This is a proxy for
    language, not a measure of it, and for THIS study it is a badly biased one:
    it would systematically exclude English-language posts originating from
    Iran, Israel and neighbouring countries - very likely the most analytically
    important posts in the sample. Rejected on validity grounds.

  * Drop the language criterion. That changes the study design and would need
    to be justified to reviewers. Rejected.

So language is determined locally, from the caption text, after retrieval.

WHAT THIS COSTS, AND WHAT IT CANNOT DO
--------------------------------------
The cost is API quota: non-English posts are fetched and then discarded, so the
quota spent exceeds the sample obtained. The README quantifies this.

The limitation is accuracy. Statistical language identification is unreliable
on short strings, and TikTok captions are often a handful of words plus emoji
and hashtags. This module therefore:

  * strips hashtags, @mentions, URLs and emoji before detecting, because those
    are language-neutral noise that actively misleads the detector;
  * refuses to judge text shorter than a minimum length, and treats such posts
    according to an explicit, configurable policy rather than guessing;
  * records its decision for every post, so exclusions are auditable.

Every design choice here is a methodological one and should be reported in the
methods section, including the detector used and the minimum-length threshold.
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# Captions shorter than this (after cleaning) are considered too short to
# classify reliably. Detectors will happily return a confident answer for a
# three-letter string, and that answer is close to worthless.
MIN_TEXT_LENGTH = 20

# --- Cleaning patterns -----------------------------------------------------
# Applied before detection. Each removes text that carries no language signal
# but that measurably distorts the detector's output.

_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+")
_MENTION_PATTERN = re.compile(r"@[\w.]+")
_HASHTAG_PATTERN = re.compile(r"#\w+")

# Emoji and pictographic symbols. These are language-neutral, and a caption
# that is mostly emoji will otherwise be classified essentially at random.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F5FF"  # symbols and pictographs
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F680-\U0001F6FF"  # transport and map symbols
    "\U0001F700-\U0001F77F"  # alchemical symbols
    "\U0001F900-\U0001F9FF"  # supplemental symbols and pictographs
    "\U00002600-\U000026FF"  # miscellaneous symbols
    "\U00002700-\U000027BF"  # dingbats
    "\U0001FA70-\U0001FAFF"  # extended-A symbols
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "]+",
    flags=re.UNICODE,
)


def clean_for_detection(text: str) -> str:
    """Strip language-neutral noise from a caption before detecting.

    Order matters: URLs are removed first, because a URL can contain something
    that looks like a mention or a hashtag and would otherwise leave fragments
    behind.

    Args:
        text: The raw caption.

    Returns:
        The caption with URLs, mentions, hashtags and emoji removed, and
        whitespace collapsed.

    Example:
        >>> clean_for_detection("Breaking news from the region \U0001F525 #Iran @user")
        'Breaking news from the region'
    """
    cleaned = _URL_PATTERN.sub(" ", text)
    cleaned = _MENTION_PATTERN.sub(" ", cleaned)
    cleaned = _HASHTAG_PATTERN.sub(" ", cleaned)
    cleaned = _EMOJI_PATTERN.sub(" ", cleaned)

    # Collapse the runs of whitespace left behind by the substitutions above.
    return " ".join(cleaned.split())


class LanguageFilter:
    """Decides whether a caption is in the study's target language.

    Usage:
        filter = LanguageFilter(target_language="en", mode="detect")
        keep, reason = filter.matches("Breaking news from the region tonight")
    """

    def __init__(
        self,
        target_language: str = "en",
        mode: str = "detect",
        keep_undetermined: bool = True,
    ) -> None:
        """Configure the filter.

        Args:
            target_language:   ISO 639-1 code the sample is restricted to.
            mode:              "detect" runs language identification on each
                               caption. "off" accepts every post, leaving
                               language filtering to the analysis stage.
            keep_undetermined: What to do with captions too short to classify.
                               True (the default) keeps them, which is the
                               conservative choice for a study of this kind: it
                               is better to over-collect and filter later than
                               to discard data irretrievably at collection
                               time, since the posts cannot be re-fetched once
                               deleted. Set False for a stricter sample.

        The detector is imported lazily so that `langdetect` is an OPTIONAL
        dependency: a researcher running in mock mode, or filtering at the
        analysis stage instead, need not install it.
        """
        self.target_language = target_language.lower()
        self.mode = mode.lower()
        self.keep_undetermined = keep_undetermined

        self._detector = None
        self._detector_unavailable = False

        # Counters for the end-of-run report. Every exclusion in a research
        # dataset should be countable and reportable.
        self.checked = 0
        self.accepted = 0
        self.rejected = 0
        self.undetermined = 0

        if self.mode == "detect":
            self._load_detector()

    def _load_detector(self) -> None:
        """Import and configure `langdetect`, tolerating its absence."""
        try:
            from langdetect import DetectorFactory, detect_langs  # type: ignore

            # langdetect is randomised by default and returns different answers
            # for the same input across runs. Seeding it makes the study's
            # inclusion decisions reproducible - essential for a dataset that
            # has to be defensible.
            DetectorFactory.seed = 0

            self._detector = detect_langs
            logger.info(
                "Language filter active: keeping '%s' captions "
                "(detector: langdetect, seeded for reproducibility).",
                self.target_language,
            )
        except ImportError:
            self._detector_unavailable = True
            logger.warning(
                "STUDY_LANGUAGE_MODE=detect but the 'langdetect' package is "
                "not installed, so language filtering is DISABLED and every "
                "post will be kept regardless of language. Install it with: "
                "pip install langdetect"
            )

    def matches(self, caption: str) -> Tuple[bool, str]:
        """Decide whether `caption` belongs in the sample.

        Args:
            caption: The post's caption text.

        Returns:
            (keep, reason) - `keep` is the decision, `reason` is a short string
            explaining it, which the pipeline writes to the log so that any
            exclusion can be reconstructed and audited afterwards.
        """
        self.checked += 1

        # --- Mode "off", or the detector could not be loaded ---------------
        if self.mode == "off" or self._detector_unavailable or self._detector is None:
            self.accepted += 1
            return True, "language filtering disabled"

        cleaned = clean_for_detection(caption)

        # --- Too short to classify -----------------------------------------
        if len(cleaned) < MIN_TEXT_LENGTH:
            self.undetermined += 1
            if self.keep_undetermined:
                self.accepted += 1
                return True, f"caption too short to classify ({len(cleaned)} chars), kept"
            self.rejected += 1
            return False, f"caption too short to classify ({len(cleaned)} chars), dropped"

        # --- Run the detector ----------------------------------------------
        try:
            # `detect_langs` returns ranked candidates with probabilities,
            # rather than `detect`'s single best guess. The probability is
            # worth having: it allows a confidence threshold to be applied and
            # reported, and it is logged for borderline cases.
            candidates = self._detector(cleaned)
        except Exception as exc:
            # langdetect raises on input it cannot handle at all (for example,
            # text consisting only of digits or punctuation). Treat that as
            # undetermined rather than as a rejection.
            self.undetermined += 1
            logger.debug("Language detection failed on %r: %s", cleaned[:60], exc)
            if self.keep_undetermined:
                self.accepted += 1
                return True, "language undetectable, kept"
            self.rejected += 1
            return False, "language undetectable, dropped"

        if not candidates:
            self.undetermined += 1
            self.accepted += 1
            return True, "no language candidates returned, kept"

        best = candidates[0]
        detected = str(best.lang).lower()

        if detected == self.target_language:
            self.accepted += 1
            return True, f"detected {detected} (p={best.prob:.2f})"

        self.rejected += 1
        return False, f"detected {detected} (p={best.prob:.2f}), not {self.target_language}"

    def summary(self) -> str:
        """Return a one-line report of this filter's decisions.

        Written to the log at the end of a run. The rejection count is a number
        the methods section needs: it describes how much of what the API
        returned was discarded, and why.
        """
        if self.mode == "off" or self._detector_unavailable:
            return f"Language filter: disabled ({self.checked} posts passed through)."
        return (
            f"Language filter ({self.target_language}): "
            f"{self.checked} checked, {self.accepted} kept, "
            f"{self.rejected} rejected, {self.undetermined} undetermined."
        )
