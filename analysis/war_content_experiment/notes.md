# War-content drill-down experiment - running notes

Goal: same method as the freeiran-cluster experiment (analysis/ai_label_experiment/),
now aimed specifically at war/military content - missiles, airstrikes, attacks -
rather than the protest/human-rights cluster. Language filter off throughout.
Duration<=30s, max_posts_per_account=1, comments disabled, target=50/round
unless noted - all carried over from what worked in the first experiment.

## Reference point (fixed, not re-run)

The current "best war-focused" hashtag config from the prior experiment:
freeiran, freelran, standwithiran, standwithlran, standwuthiran, iranwar,
usairanwar, usavsiran, iranuswar, iranvsusa (10 tags) + min_comments=10 +
language off (= run 12/13 in the other log) -> **30% AI-label rate**.
This is the number every round here needs to beat to justify moving further
toward war-specific vocabulary instead of the freeiran cluster.

## Round 1 (seed - keyword search, require_ai_label=TRUE)

**Params:** keywords = missile, airstrike, drone strike, explosion, bombing,
military attack | hashtags empty | require_ai_label=true | duration<=30s |
max_posts_per_account=1 | min_comments=0 | language off | target=50

**Result:** 50/50 saved, 0 errors, 972 raw candidates (by far the largest
pool of the whole project - generic war vocabulary is common).

**Findings - major contamination problem:**
- **Only 9/50 (18%) actually mention Iran, Israel, or a related conflict
  actor.** The other 41 are generic AI-generated action/meme content that
  happens to use war vocabulary for shock value or as a generic AI-demo
  subject: "Amazing pizza explosion", "SpongeBob tv explosion", "meteor
  explosion", "Explosion inside Town Roblox Train RNG", a generic "military
  attack helicopter flies low over a desert" B-roll clip with no stated
  connection to any real conflict.
- This makes sense in hindsight: "explosion", "missile", "bombing" are
  exactly the kind of generic, high-drama subjects AI video generators (Sora
  etc.) get used for as tech demos, independent of any news topic. Bare war
  vocabulary is not a conflict-specific signal on its own.
- Of the genuinely relevant 9: real examples include Iran's underground
  missile cities (Yazd), a Yemen Houthi attack on Israel, US-Israel military
  action against Iran, Dubai drone-strike filming ban, UAE consulate
  drone-strike-near-miss in Erbil - a believable spread of real
  conflict-adjacent AI content once relevance is filtered for.
- ai_label_kind skewed toward ai_generated (78%) over creator_labelled
  (22%), similar ratio to the freeiran-cluster experiment.

**Decision for round 2:** pair war vocabulary with conflict-specific context
directly in the keyword phrase, rather than searching generic terms alone -
"iran missile", "iran airstrike", "israel strikes iran", "tehran attack",
"iran drone strike". Drop require_ai_label (natural search from here, per
the established methodology).

## Round 2 (conflict-specific compound keyword phrases)

**Params:** keywords = "iran missile", "iran airstrike", "israel strikes
iran", "tehran attack", "iran drone strike" | require_ai_label=false |
target=50

**Result:** 50/50 saved, 253 raw candidates, 198 ineligible, 5 account-cap.

**Findings - the opposite extreme:**
- **Topical relevance: 100/100%** - every post mentions Iran/Israel/a named
  conflict actor. Compound conflict-specific phrasing completely solves the
  contamination problem from round 1.
- **AI-label rate: 2%** (1/50) - collapsed almost to zero. The top post by
  far is @apnews (Associated Press's own account, 6.9M views) reporting a
  real drone strike on a Dubai airport fuel tank - this is genuine
  journalism, not AI content, and it's representative of the whole sample:
  specific, factual, datelined war reporting.
- **The real pattern emerging:** AI-generated content about this conflict
  tends to use GENERIC, decontextualized war vocabulary (a generic "missile"
  or "explosion" video with no specific verifiable event attached - easy for
  a generator or a human prompting one to produce). Real reporting uses
  SPECIFIC, factual event descriptions (which airport, which fuel tank,
  which country) that AI generators/casual posters don't naturally produce
  without a real event to describe. Specificity of language appears to be a
  proxy for authenticity here, inversely related to AI-density.

**Decision for round 3:** round 1's generic single-word terms were forced to
require_ai_label=true, so its 18% relevance figure says nothing about the
NATURAL co-occurrence of relevance and AI content at those terms. Re-run
round 1's exact keyword set naturally (require_ai_label=false) to get a
clean baseline: what fraction of natural "missile/explosion/airstrike"
content is both relevant AND AI-labeled, without forcing anything.

## Round 3 (round 1's generic keywords, natural/unforced)

**Params:** keywords = missile, airstrike, drone strike, explosion, bombing,
military attack (same as round 1) | require_ai_label=false | target=50

**Result:** 50/50 saved, 72 raw candidates, 22 ineligible.

**Findings - the cleanest negative result of this sub-experiment:**
- Topically relevant: 12/50 (24%)
- AI-labeled: 4/50 (8%)
- **Both relevant AND AI-labeled: 0/50 (0%)**
- In this keyword-vocabulary space, "relevant" and "AI-labeled" are
  essentially disjoint sets. The AI-labeled posts here are generic content
  ("#missile #Missile💪 #🚀🚀🚀🚀..." emoji spam, an AI-tagged "explosion"
  meme) with zero conflict relevance; the relevant posts (mentioning
  Iran/Israel/a real location) are plain captions with no AI label - mostly
  looking like real bystander/news-adjacent posting, not AI generation.
- Combined with round 2 (100% relevant, 2% AI), the picture is now
  consistent across two different tests: generic war vocabulary and
  conflict-specific war vocabulary each fail, in opposite ways, to find a
  population that is both relevant and AI-dense.

**Decision for round 4:** stop searching generic KEYWORD vocabulary
entirely. Switch to dedicated war-topic HASHTAGS instead of free-text terms
- iranwar, iranisraelwar, worldwar3, ww3, warupdate. Earlier this session
(before the duration-cap/account-cap refinements existed), #iranwar alone
scored a 12-18% AI-label rate on its own - notably higher than anything
found in this keyword sub-experiment so far. Worth re-testing with the
refinements now proven to matter (duration<=30s, 1 post/account).

## Round 4 (dedicated war hashtags: iranwar, iranisraelwar, worldwar3, ww3, warupdate)

**Params:** hashtags = iranwar, iranisraelwar, worldwar3, ww3, warupdate |
require_ai_label=false | duration<=30s | max_posts_per_account=1 | target=50

**Result:** 50/50 saved, 97 raw candidates, 47 ineligible.

**Findings - first real overlap found:**
- Topically relevant: 31/50 (62%)
- AI-labeled: 4/50 (8%)
- **Both relevant AND AI-labeled: 3/50 (6%)** - small, but the first
  non-zero overlap in this sub-experiment. Dedicated war hashtags clearly
  beat free-text keyword search for finding this intersection.
- All 3 hits carry #iranisraelwar specifically (of the 5 tags tested). One
  is a genuinely new content type: a structured, bullet-pointed "1.
  Background of the Plan..." AI-generated news-explainer summarizing a US
  peace proposal - a different register from the freeiran cluster's
  templated protest captions. The other two look more like generic
  AI-labeled content that happened to tag #iranisraelwar for reach
  (one mass-tags a dozen Pakistani politician accounts; one is an unrelated
  personal/family post that tacked the tag on).

**Decision for round 5:** isolate #iranisraelwar alone (drop the other 4
tags) to test whether it specifically carries the higher AI-density, or
whether 6% was a fluke of this particular 50-post draw.

## Round 5 (#iranisraelwar alone)

**Params:** hashtags = iranisraelwar (only) | require_ai_label=false |
target=50

**Result:** 50/50 saved, 112 raw candidates, 61 ineligible.

**Findings:**
- Topically relevant: 50/50 (100%, as expected - it's the topic tag itself)
- AI-labeled: 3/50 (6%)
- Both: 3/50 (6%)
- **This exactly matches round 4's 6% overlap rate.** Confirms 6% isn't
  noise from one draw - #iranisraelwar has a real, stable, but low AI-content
  density. Isolating it further doesn't concentrate it any more than the
  5-tag set did; the other 4 tags in round 4 were just adding irrelevant
  volume without hurting or helping the AI-relevant intersection.

**checkpoint after 5 of 10 rounds - reporting to user before continuing.**
