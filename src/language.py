"""
Language detection for the English-only inclusion criterion.

The TikTok Research API's video-query endpoint has no server-side language
filter - it accepts conditions on only eleven fields, none of which
describes language - so language is determined locally, from the caption
text, after retrieval.

Filtering by `region_code` instead was considered and rejected: it would be
a biased proxy for this study, systematically excluding English-language
posts from Iran, Israel and the wider region. Dropping the criterion
entirely was also rejected, since it would change the study design.

The cost is API quota: non-English posts are fetched and then discarded (see
SEARCH_OVERFETCH_FACTOR in the README). The limitation is accuracy:
statistical language ID is unreliable on short strings, so this module
strips hashtags, mentions, URLs and emoji before detecting, and applies an
explicit, configurable policy to captions too short to classify. Every
decision is logged so exclusions are auditable.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


# Captions shorter than this (after cleaning) are considered too short to
# classify reliably.
MIN_TEXT_LENGTH = 20

# --- Cleaning patterns -----------------------------------------------------
# Removed before detection: language-neutral text that would otherwise
# mislead the detector.

_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+")
_MENTION_PATTERN = re.compile(r"@[\w.]+")
_HASHTAG_PATTERN = re.compile(r"#\w+")

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

    URLs are removed first, since a URL can contain something that looks
    like a mention or a hashtag and would otherwise leave fragments behind.

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
    return " ".join(cleaned.split())


class LanguageFilter:
    """Decides whether a caption is in the study's target language.

    Usage:
        filter = LanguageFilter(target_languages=["en"], mode="detect")
        keep, reason = filter.matches("Breaking news from the region tonight")
    """

    def __init__(
        self,
        target_languages: Union[str, List[str]] = "en",
        mode: str = "detect",
        keep_undetermined: bool = True,
    ) -> None:
        """Configure the filter.

        Args:
            target_languages:  ISO 639-1 code(s) the sample is restricted
                               to - a single code or a list. A caption
                               matching any one of them is kept.
            mode:              "detect" runs language identification on each
                               caption. "off" accepts every post.
            keep_undetermined: Whether to keep captions too short to
                               classify (the conservative choice: a post
                               dropped at collection time can never be
                               recovered, one collected in error can still
                               be filtered later). Set False for a
                               stricter sample.

        The detector is imported lazily so `langdetect` stays an optional
        dependency for anyone running in mock mode or filtering later.
        """
        if isinstance(target_languages, str):
            target_languages = [target_languages]
        self.target_languages = {lang.lower() for lang in target_languages}
        self.mode = mode.lower()
        self.keep_undetermined = keep_undetermined

        self._detector = None
        self._detector_unavailable = False

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

            # langdetect is randomised by default; seeding makes the study's
            # inclusion decisions reproducible.
            DetectorFactory.seed = 0

            self._detector = detect_langs
            logger.info(
                "Language filter active: keeping %s captions "
                "(detector: langdetect, seeded for reproducibility).",
                ", ".join(sorted(self.target_languages)),
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
            (keep, reason) - `keep` is the decision, `reason` is a short
            string logged by the pipeline so exclusions can be audited.
        """
        self.checked += 1

        if self.mode == "off" or self._detector_unavailable or self._detector is None:
            self.accepted += 1
            return True, "language filtering disabled"

        cleaned = clean_for_detection(caption)

        if len(cleaned) < MIN_TEXT_LENGTH:
            self.undetermined += 1
            if self.keep_undetermined:
                self.accepted += 1
                return True, f"caption too short to classify ({len(cleaned)} chars), kept"
            self.rejected += 1
            return False, f"caption too short to classify ({len(cleaned)} chars), dropped"

        try:
            # detect_langs returns ranked candidates with probabilities,
            # which lets a confidence threshold be applied and logged.
            candidates = self._detector(cleaned)
        except Exception as exc:
            # langdetect raises on input it cannot handle at all (e.g. text
            # that is only digits or punctuation); treat as undetermined.
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

        if detected in self.target_languages:
            self.accepted += 1
            return True, f"detected {detected} (p={best.prob:.2f})"

        self.rejected += 1
        langs = ", ".join(sorted(self.target_languages))
        return False, f"detected {detected} (p={best.prob:.2f}), not in [{langs}]"

    def summary(self) -> str:
        """Return a one-line report of this filter's decisions."""
        langs = ", ".join(sorted(self.target_languages))
        if self.mode == "off" or self._detector_unavailable:
            return f"Language filter: disabled ({self.checked} posts passed through)."
        return (
            f"Language filter ({langs}): "
            f"{self.checked} checked, {self.accepted} kept, "
            f"{self.rejected} rejected, {self.undetermined} undetermined."
        )
