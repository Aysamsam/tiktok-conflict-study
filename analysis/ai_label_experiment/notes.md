# AI-label population-discovery experiment - running notes

Goal: find the hashtag/keyword "population" that naturally yields the highest
concentration of AI-labeled content related to the Israel-Iran conflict.
Constraints held throughout: video_duration <= 30s, no comments collected.

## Run 1 (seed run - require_ai_label=TRUE)

**Params:** hashtags = usairanwar, iran, war, iran(flag), usavsiran, iranuswar,
freeiran | require_ai_label=true | max_video_duration=30s | min_comments=0 |
target=50 | random_order=true | sort_by=none

**Result:** 50/50 saved, 0 errors, 799 raw candidates scanned across the 7
stratified hashtag searches.

**Findings:**
- Hashtag frequency within the AI-labeled pool: #freeiran 46%, #iranprotests
  36%, #standwithiran 34%, #humanrights 28%, #womanlifefreedom 26%. The
  original "war" tags (#iranuswar, #usairanwar, #usavsiran) each only 4-8%.
- 22% of posts (11/50) share a near-identical templated caption ("People of
  Iran raise their voices for freedom, justice, and human rights...") across
  unrelated accounts (khan2001251, muhramadhan006, fallen_la414, aicapsule0,
  iran.military06...) - a strong signature of templated/coordinated AI content
  specifically inside the freeiran/iranprotests/standwithiran/humanrights/
  womanlifefreedom cluster.
- ai_label_kind: 84% ai_generated (platform-detected), 16% creator_labelled.
- Engagement is low and skewed: median views 771, median comments 1 - AI
  content here is small-account, low-reach, consistent with earlier findings
  this session that AI-labeled content has structurally lower view counts.
- Noise: a handful of stock-market posts (#investing, #stockmarket) rode in
  on generic "iran"/"war" co-mentions - unrelated content contaminating the
  broad tags.

**Caveat:** run 1 forced require_ai_label=true, so hashtag frequency here
conflates "this hashtag has an inherently high AI-content rate" with "this
hashtag just has a large raw volume" (e.g. #iran is huge in volume generally,
per earlier session findings, so its 20% share here isn't necessarily evidence
of AI-density). The real test is run 2: search the discovered cluster
WITHOUT the require_ai_label filter and measure the resulting AI-label rate
directly against the ~3-18% baseline seen in earlier unfiltered runs this
session.

**Decision for run 2:** drop require_ai_label (per instruction). Narrow the
hashtag set to the cluster discovered here - freeiran, iranprotests,
standwithiran, humanrights, womanlifefreedom - dropping the generic
iran/war/usa-vs-iran tags entirely, to isolate whether this specific cluster
is inherently AI-content-dense or just AI-content-voluminous.

## Run 2 (freeiran/protest cluster, require_ai_label=FALSE)

**Params:** hashtags = freeiran, iranprotests, standwithiran, humanrights,
womanlifefreedom | require_ai_label=false | max_video_duration=30s |
overfetch=25 | target=50

**Result:** 50/50 saved, 0 errors, only 62 raw candidates existed for this
narrow cluster (small underlying pool - most of it got kept: 50/62 = 81%
passed local eligibility).

**Findings - this is the key result of the whole experiment so far:**
- **AI-label rate: 48%** (22 ai_generated + 2 creator_labelled, out of 50) -
  with NO require_ai_label filter applied. Compare to the ~3-18% baseline
  from earlier hashtag/keyword populations this session (#iranwar-only was
  12-18%, the 6-hashtag war-focused set was 3.3%). This confirms the cluster
  is inherently AI-content-dense, not merely AI-content-voluminous - the run
  1 caveat is resolved.
- The templated caption ("People of Iran raise their voices for freedom,
  justice, and human rights...") reappeared in 11/50 posts (22%), posted by
  11 different, apparently unrelated accounts across the US/GB/BD - strong
  evidence of a coordinated/templated content campaign specifically using
  this hashtag cluster.
- **New signal: moderation-evasion hashtag variants.** Misspelled variants
  of the core tags appeared: #freelran, #standwithlran ("l" swapped for "i"),
  #humenrights, #standwuthiran. These read as deliberate typo-variants to
  dodge hashtag-based moderation/shadow-banning - a marker of coordinated
  inauthentic-style amplification worth testing directly.
- Region shifted from run 1: Bangladesh (BD, 24%) and Pakistan (PK, 24%) now
  prominent alongside US (28%) - broader/less US-centric than run 1's
  AI-forced sample.
- Engagement is bimodal: median views only 257, but one post
  (@editz.empire) hit 1.48M views - a small number of outlier posts carry
  most of the reach; most of the population is low-reach.

**Decision for run 3:** test whether the templated CAPTION ITSELF (not just
the hashtag cluster) defines an even higher-purity AI-content population.
Switch to a keyword-only search (hashtags empty) on caption fragments from
the template ("people of iran", "raise their voices"), to see if targeting
the literal recurring phrase - regardless of which hashtag variant a post
uses - pushes the AI-label concentration even higher than 48%.

## Run 3 (keyword-only: "people of iran" / "raise their voices")

**Params:** keywords = "people of iran", "raise their voices" | hashtags
empty | require_ai_label=false | max_video_duration=30s | target=50

**Result:** 50/50 saved, 0 errors, 82 raw candidates, 32 ineligible.

**Findings:**
- **AI-label rate: 42%** (21/50, all ai_generated, 0 creator_labelled this
  time) - strong, but slightly BELOW run 2's 48%. The keyword match is
  looser than the hashtag cluster: it also catches captions that merely
  mention "people of Iran" in an unrelated, non-templated way (e.g. "I stand
  with the people of Iran"), diluting purity slightly.
- Region shifted sharply to Pakistan (48%, up from 24% in run 2) - the
  keyword search surfaces a distinct, more Pakistan-centric echo of the
  template than the hashtag search did.
- Engagement dropped further: median views only 173 (vs 257 in run 2, 771 in
  run 1) - keyword-only search reaches even smaller/more obscure accounts.
- Many matching posts carry NO hashtags at all - pure caption-template
  propagation without any tagging, which a hashtag-only search (run 2) would
  have missed entirely. Confirms hashtags and keywords surface genuinely
  different slices of the same underlying campaign.
- Repeated near-identical captions again dominate (12/50, 24%), including
  3 posts from the same account (@ioroparko) - even more concentrated
  template reuse than run 2.

**Comparison so far: run 2 (hashtag cluster) is still the best population
found (48% > 42%).**

**Decision for run 4:** test the moderation-evasion misspelled hashtag
variants directly, in isolation - #freelran, #standwithlran, #humenrights,
#standwuthiran - discovered in run 2 as low-frequency co-occurring tags.
Hypothesis: a campaign deliberately misspelling tags to dodge moderation
might concentrate AI content even more than the "clean" tag spellings.

## Run 4 (misspelled-variant hashtags: freelran, standwithlran, humenrights, standwuthiran)

**Params:** hashtags = freelran, standwithlran, humenrights, standwuthiran |
require_ai_label=false | max_video_duration=30s | target=50

**Result:** 50/50 saved, 0 errors, 59 raw candidates - a real, non-trivial
population (hypothesis of a "rare, hidden" tag set was wrong; these tags are
about as common as the clean ones).

**Findings:**
- **AI-label rate: 42%** (20 ai_generated + 1 creator_labelled) - matches
  run 3, still below run 2's 48%. The misspelling hypothesis did NOT beat
  the clean-tag cluster.
- Heavy overlap with run 2's population: 38-52% of these posts ALSO carry
  the clean tags (#iranprotests 50%, #womanlifefreedom 52%, #humanrights
  38%) - the misspelled tags are mostly used ALONGSIDE the clean ones by the
  same campaign/accounts, not a separate hidden population.
- **Contamination source identified:** #humenrights pulled in an unrelated
  Vietnamese vlogger's posts (@ongngayngo, "justiceforyumenglong") with
  nothing to do with Iran - "humenrights" is apparently a generic misspelling
  reused by other unrelated campaigns too, diluting purity.
- Strong account concentration: @nayar_irani (6 posts), @iran_miltary2 (5),
  @iran2134 (3) - a handful of accounts account for a large share of this
  population, more so than run 2. Relevant for `STUDY_MAX_POSTS_PER_ACCOUNT`
  if diversity matters more than raw AI-rate in the final dataset.

**Standings so far: run 2 (48%) > run 3 (42%) = run 4 (42%).**

**Decision for run 5:** drop the generic #humanrights/#humenrights tags
entirely (the identified contamination source) and keep only the
Iran-uprising-specific tags: freeiran, freelran, standwithiran,
standwithlran, iranprotests, lranprotests, standwuthiran, womanlifefreedom,
womenlifefreedom. Tests whether removing the generic/contaminated tags
raises the AI-label concentration of the remaining cluster above 48%.

## Run 5 (9-tag Iran-specific cluster, humanrights/humenrights dropped from search)

**Params:** hashtags = freeiran, freelran, standwithiran, standwithlran,
iranprotests, lranprotests, standwuthiran, womanlifefreedom,
womenlifefreedom | require_ai_label=false | max_video_duration=30s |
target=50

**Result:** 50/50 saved, 0 errors, only 52 raw candidates - this cluster's
true pool is nearly exhausted at n=50-52.

**Findings - new best result:**
- **AI-label rate: 54%** (26 ai_generated + 1 creator_labelled) - the best
  of the experiment so far, beating run 2's 48%. Note #humanrights still
  appears at 52% frequency as a CO-OCCURRING tag (it just wasn't searched
  for directly) - confirms it's a natural companion tag of this cluster, not
  something that needed excluding from co-occurrence, only from the search
  terms themselves.
- New variant family spotted: #lran, #lrano (2% each) - substituting "Iran"
  itself (not just compound words like "freeIran"/"standwithIran") with
  "Lran". More aggressive evasion than the previously tested variants.
- Templated caption still dominant: 17/50 (34%) share near-identical
  wording, now the single largest share observed across all runs.
- Engagement stayed low (median 287 views) but comment_count showed one
  extreme outlier (2,814) worth spot-checking for authenticity.

**Standings: run 5 (54%) > run 2 (48%) > run 3 (42%) = run 4 (42%).**

**Decision for run 6:** isolate the newly spotted "Lran" root-substitution
family - lran, lrano, lranprotests, standwithlran - to test whether
substituting the country name itself (not just compound hashtags) marks an
even more concentrated pocket of coordinated AI content.

## Run 6 (Lran root-substitution family: lran, lrano, lranprotests, standwithlran)

**Params:** hashtags = lran, lrano, lranprotests, standwithlran |
require_ai_label=false | max_video_duration=30s | target=50

**Result:** 50/50 saved, 0 errors, 55 raw candidates.

**Findings:**
- **AI-label rate: 44%** (16 ai_generated + 6 creator_labelled) - below run
  5's 54%, though creator_labelled share (12%) is notably higher than any
  other run (usually 2-4%) - this specific family attracts more
  self-disclosed AI content.
- **Important methodological finding: TikTok's has_ai_label undercounts
  actual coordinated/synthetic content.** 9/50 posts share an identical
  caption ("Lran \U0001F1EE\U0001F1F7The people of Iran are speaking outG
  loudly for freedom,justice and human rights...", including the same
  mid-word capitalization typo "outG" - a copy-paste artifact) posted by
  accounts following a `firstname.lastname.ffNN` naming pattern (e.g.
  erin.stevens.ff06, bailey.mackey.ff03, esme.rice.ff07,
  ainsley.justice.ff02) - a strong bot-farm signature. Yet 6 of those 9 have
  has_ai_label=False. This means the true prevalence of coordinated/synthetic
  content in this population is HIGHER than the has_ai_label rate suggests -
  the AI-label rate should be read as a lower bound, not the full picture.
- Account concentration is severe here: @vl4t63 alone posted 7/50 (14%) of
  the sample - a single account, not a broad population. Any final
  recommended config should consider `STUDY_MAX_POSTS_PER_ACCOUNT` to avoid
  one prolific account dominating the sample.
- Region shifted to heavily US (58%, the highest of any run).

**Standings: run 5 (54%) > run 2 (48%) > run 6 (44%) > run 3 (42%) = run 4 (42%).**

**Decision for run 7:** robustness-check the run-5 winning config (the 9-tag
Iran-uprising cluster) with STUDY_MAX_POSTS_PER_ACCOUNT=1, to confirm the 54%
result isn't an artifact of a few prolific accounts and holds up under
one-post-per-account diversity.

## Run 7 (run 5's 9-tag cluster + STUDY_MAX_POSTS_PER_ACCOUNT=1)

**Params:** same 9-tag hashtag cluster as run 5 | max_posts_per_account=1 |
require_ai_label=false | max_video_duration=30s | target=50

**Result:** 50/50 saved, 0 errors, 68 raw candidates, 10 skipped specifically
for exceeding the account cap (confirms the cap was actively enforced, not a
no-op).

**Findings - new best result, and the most robust one:**
- **AI-label rate: 58%** (23 ai_generated + 6 creator_labelled) - the best
  of the whole experiment, AND achieved with zero duplicate accounts (0
  accounts with >1 post - full diversity). This is stronger evidence than
  run 5's 54% because it can't be attributed to a few prolific accounts
  padding the count either way.
- creator_labelled share (12%) matches run 6's high point - the account cap
  seems to surface a healthier mix of platform-detected AND self-disclosed
  AI content, not just one kind.
- Confirms: run 5's finding was real, not a prolific-account artifact -
  capping accounts actually IMPROVED the measured rate.

**Standings: run 7 (58%) > run 5 (54%) > run 2 (48%) > run 6 (44%) > run 3 (42%) = run 4 (42%).**

**Recommended config so far:** hashtags = freeiran, freelran, standwithiran,
standwithlran, iranprotests, lranprotests, standwuthiran, womanlifefreedom,
womenlifefreedom | max_posts_per_account=1 | max_video_duration=30s |
require_ai_label=false.

**Decision for run 8:** isolate the contribution of the ORIGINAL
max_video_duration=30s constraint itself (present since run 1, never
tested against its absence). Re-run the run-7 config with the duration cap
REMOVED, to check whether restricting to <=30s content actually helped,
hurt, or was neutral to the AI-label concentration.

## Run 8 (run 7's config, duration cap REMOVED)

**Params:** same 9-tag cluster + max_posts_per_account=1 | max_video_duration
UNLIMITED (was 30s) | require_ai_label=false | target=50

**Result:** 50/50 saved, 0 errors, 53 raw candidates, 3 skipped for account
cap (durations now range up to 54s, vs a hard 30s ceiling before).

**Findings - major result: the duration cap matters a lot:**
- **AI-label rate: 40%** (16 ai_generated + 4 creator_labelled) - a drop of
  18 percentage points from run 7's 58%, with every other parameter held
  identical. This is the largest single swing in the whole experiment.
- Conclusion: the original max_video_duration<=30s constraint is NOT an
  arbitrary restriction - it is actively concentrating the sample toward
  AI-generated content. Longer videos on these same hashtags (up to 54s
  here) are diluted with more organic/human content. Short-form AI clips
  (typical of current text-to-video tools) cluster at the low end of TikTok's
  duration range, so capping duration is itself a meaningful filter for this
  research question, not just a scope-narrowing convenience.
- **Combined recommendation confirmed: keep max_video_duration=30s.**

**Standings: run 7 (58%, capped duration) > run 5 (54%) > run 2 (48%) >
run 6 (44%) > run 3 (42%) = run 4 (42%) > run 8 (40%, uncapped duration).**

**Decision for run 9:** restore max_video_duration=30s. Test widening the
winning 9-tag hashtag cluster by adding the "Lran" root-substitution family
(lran, lrano) discovered in run 6, to see whether the population can be
grown in volume/coverage while holding the AI-label concentration near 58%.

## Run 9 (9-tag cluster + lran, lrano added = 11 tags; duration+account cap restored)

**Params:** 9-tag cluster + lran + lrano | max_posts_per_account=1 |
max_video_duration=30s | require_ai_label=false | target=50

**Result:** 50/50 saved, 0 errors, 63 raw candidates, 4 account-cap skips,
0 duplicate accounts in the final sample.

**Findings:**
- **AI-label rate: 48%** (22 ai_generated + 2 creator_labelled) - DOWN from
  run 7's 58%. Widening the cluster with the lran/lrano tags diluted the
  concentration back to roughly run 2/run 9 parity, even though the lran
  family tested reasonably on its own in run 6 (44%). Adding tags doesn't
  compose additively - the narrower 9-tag set is the purer population.
- **Conclusion: run 7's narrower 9-tag config remains the best found.**
  Broadening hashtag coverage trades purity for volume; for a research
  question about AI-content CONCENTRATION specifically, narrower wins.

**FINAL STANDINGS (all 9 completed test runs, n=50 each except run 1 which
was label-forced by construction):**
1. **Run 7: 58%** - 9-tag cluster + max_posts_per_account=1 + duration<=30s <- BEST
2. Run 5: 54% - same 9-tag cluster, no account cap
3. Run 9: 48% - 11-tag cluster (9-tag + lran/lrano), account cap + duration cap
3. Run 2: 48% - same 5 of the 9 tags (no lranprotests/standwuthiran/freelran/standwithlran), no account cap
5. Run 6: 44% - Lran root-family alone (lran, lrano, lranprotests, standwithlran)
6. Run 3: 42% - keyword-only ("people of iran", "raise their voices")
6. Run 4: 42% - misspelled-variant tags alone (freelran, standwithlran, humenrights, standwuthiran)
8. Run 8: 40% - run 7's config MINUS the duration cap (proves the cap matters)
(Baseline from earlier in the session, different hashtags entirely: 3.3-18%)

**RECOMMENDED FINAL PARAMETERS:**
- Hashtags: freeiran, freelran, standwithiran, standwithlran, iranprotests,
  lranprotests, standwuthiran, womanlifefreedom, womenlifefreedom (9 tags)
- max_video_duration_seconds = 30 (keep - proven to matter, +18pp)
- max_posts_per_account = 1 (keep - improves purity AND diversity)
- require_ai_label = false (the population itself is now AI-dense; forcing
  the filter is no longer necessary to find AI content, and would prevent
  measuring true prevalence)
- min_comments = 0, comments disabled (STUDY_MAX_COMMENTS_PER_POST=0)

**Decision for run 10 (final):** re-run the winning config at a larger
target (aim for 100) as the definitive confirmatory run and final delivered
dataset, accepting the run may fall short of 100 if the true population is
smaller (observed pools for this cluster have run 52-68 raw candidates).

## Post-experiment: trading AI-density for topical relevance

After the 10-run experiment concluded, the user asked to swap the
protest-movement tags (iranprotests, lranprotests, womanlifefreedom,
womenlifefreedom) for tags more specifically about the Israel-Iran WAR
itself, since the winning cluster is arguably about a related-but-distinct
topic (Iran's domestic "Woman, Life, Freedom" protest movement) rather than
the military conflict the study is nominally about.

## Run 11 (war-tag swap: drop protest/freedom tags, add war tags)

**Params:** freeiran, freelran, standwithiran, standwithlran, standwuthiran,
iranwar, usairanwar, usavsiran, iranuswar (9 tags) | same caps as run 7/10
(max_posts_per_account=1, max_video_duration=30s) | target=50

**Result:** 50/50 saved, 68 raw... 82 raw candidates, 27 ineligible, 5
account-cap skips.

**Findings:**
- **AI-label rate: 34%** (13 ai_generated + 4 creator_labelled) - well below
  run 7's 58%, though still far above the 3.3-18% session baseline for
  war-only tags searched alone.
- The added war tags (iranwar, usairanwar, usavsiran, iranuswar) each only
  matched 6-12% of results - most posts still arrived via the retained
  freeiran/freelran/standwithiran/standwithlran/standwuthiran tags, with
  iranprotests/womanlifefreedom still appearing at 28-30% purely as natural
  co-occurring tags (not search terms) - dropping a tag from the SEARCH
  doesn't remove it from posts that also carry a still-searched tag.
- Notable direct evidence: @funny.political.c, tagged with #usavsiran
  #iranvsusa #trumpswar, explicitly states in its own caption "All content
  is AI-generated" - confirms war-tagged AI content exists, just far less
  densely than the protest cluster.

**Decision for run 12:** user requested adding iranvsusa (alongside the
already-present usavsiran) to further round out the war-tag coverage.

## Run 12 (add iranvsusa)

**Params:** run 11's 9 tags + iranvsusa (10 tags) | same caps | target=50

**Result:** 50/50 saved, 89 raw candidates, 33 ineligible, 6 account-cap skips.

**Findings:**
- **AI-label rate: 30%** (14 ai_generated + 1 creator_labelled) - lower
  still. Widening further into war/news-specific tags pulled in more
  straightforward breaking-news-style content (e.g. an IDF cluster-missile
  strike report, a Tel Aviv missile-strike report) rather than AI content.
- **Clear dose-response pattern across runs 7 -> 11 -> 12:** 58% -> 34% ->
  30% as protest/freedom tags are progressively replaced with war/news tags.
  AI-content concentration and topical "war-ness" trade off directly in this
  hashtag space - there is no config found yet that is both highly
  war-specific AND highly AI-dense simultaneously.

**Open question for the user:** which end of this tradeoff serves the study
better - the original 58% protest-cluster (higher AI density, adjacent topic)
or the ~30-34% war-focused cluster (more directly on-topic, still 2-10x the
baseline)? A middle-ground blend (keep 1-2 protest tags alongside the war
tags) has not yet been tested and could land between these two points.
