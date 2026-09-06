"""Quick-look analysis for one iteration of the AI-label experiment.

Usage: python analyze_run.py run_01_posts.csv
"""
import csv
import io
import re
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="backslashreplace")

STOPWORDS = {
    "the", "a", "an", "to", "of", "and", "is", "in", "on", "for", "with",
    "this", "that", "are", "was", "will", "be", "has", "have", "it", "we",
    "i", "you", "your", "our", "not", "but", "as", "at", "by", "from",
    "they", "their", "who", "what", "when", "how", "why", "or", "if",
    "he", "she", "his", "her", "them", "just", "so", "do", "does", "did",
    "can", "could", "would", "should", "no", "yes", "up", "out", "about",
    "all", "more", "most", "some", "than", "then", "there", "here", "now",
}


def main(path):
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    n = len(rows)
    print(f"=== {path} : {n} posts ===\n")

    # Hashtag frequency
    hashtag_counts = Counter()
    for r in rows:
        for h in r["hashtags"].split(","):
            h = h.strip().lower()
            if h:
                hashtag_counts[h] += 1
    print("--- Top 25 hashtags ---")
    for tag, c in hashtag_counts.most_common(25):
        print(f"  #{tag}: {c} ({c/n*100:.0f}%)")

    # Keyword frequency in captions (crude word split)
    word_counts = Counter()
    for r in rows:
        caption = r["caption"].lower()
        words = re.findall(r"[a-z']{3,}", caption)
        for w in words:
            if w not in STOPWORDS:
                word_counts[w] += 1
    print("\n--- Top 25 caption words (excluding stopwords) ---")
    for w, c in word_counts.most_common(25):
        print(f"  {w}: {c}")

    # Region distribution
    region_counts = Counter((r["author_region"] or "?").upper() for r in rows)
    print("\n--- Region distribution ---")
    for reg, c in region_counts.most_common(10):
        print(f"  {reg}: {c} ({c/n*100:.0f}%)")

    # AI label kind
    kind_counts = Counter(r["ai_label_kind"] for r in rows)
    print("\n--- AI label kind ---")
    for k, c in kind_counts.most_common():
        print(f"  {k}: {c} ({c/n*100:.0f}%)")

    # Engagement stats
    def stats(field):
        vals = [int(r[field]) for r in rows if r[field]]
        if not vals:
            return "n/a"
        return f"min={min(vals)} avg={sum(vals)/len(vals):.1f} median={sorted(vals)[len(vals)//2]} max={max(vals)}"

    print("\n--- Engagement ---")
    for field in ["view_count", "like_count", "comment_count", "share_count", "favorites_count", "video_duration"]:
        print(f"  {field}: {stats(field)}")

    # Duplicate/templated captions (near-identical, signals coordinated/templated content)
    caption_norm_counts = Counter()
    for r in rows:
        norm = re.sub(r"[^a-z ]", "", r["caption"].lower())
        norm = re.sub(r"\s+", " ", norm).strip()
        if len(norm) > 20:
            caption_norm_counts[norm[:80]] += 1
    dupes = {k: c for k, c in caption_norm_counts.items() if c > 1}
    print(f"\n--- Repeated/templated caption openings ({len(dupes)} found) ---")
    for k, c in sorted(dupes.items(), key=lambda x: -x[1]):
        print(f"  x{c}: {k}...")

    # Accounts
    account_counts = Counter(r["username"] for r in rows)
    repeats = {k: c for k, c in account_counts.items() if c > 1}
    print(f"\n--- Accounts with >1 post ({len(repeats)}) ---")
    for k, c in sorted(repeats.items(), key=lambda x: -x[1]):
        print(f"  @{k}: {c}")


if __name__ == "__main__":
    main(sys.argv[1])
