# Evaluation

```bash
python -m evaluation.generate_synthetic --n 500 --seed 7   # regenerate the synthetic set (needs the DB)
python -m evaluation.run_eval --all                         # writes evaluation/results/*.json and summary.md
```

Each JSON report keeps, for every case, the original transcript, the normalised transcript, the resolved address, the ground truth, the predicted components, status and confidence, so every number traces back to examples.

## Datasets

| File | N | What it is | Used for tuning? |
|---|---|---|---|
| `datasets/dev.jsonl` | 25 | Examples from the spec plus cases I iterated on during development | **Yes**, so its scores are optimistic and not reported as accuracy |
| `datasets/test.jsonl` | 108 | Realistic inputs across 20+ states: correct addresses, misspellings, STT-style errors, number words, Hinglish, Hindi/Kannada/Tamil script, abbreviations, landmarks, incomplete, ambiguous, PIN conflicts, rural, and 14 **coverage-gap** places India Post does not list | Written before tuning; first-run results reported |
| `datasets/synthetic_stt.jsonl` | 500 | Generated: random delivery post offices (stratified over 36 states/UTs) with STT-like corruption (dropped vowels, aspirates, doubled consonants, split/merged words, v/w, vowel length), optional district/state/PIN context (PIN sometimes as spoken digits) | No; labelled synthetic |

Ground-truth localities use India Post spellings. Where my first label differed from the data (e.g. "RS Puram" → "RSPuram East", "Ooty" → "Udagamandalam"), the label was corrected from the data, never from the system's output. Each such row has a `notes` field.

## Metrics

| Metric | Definition |
|---|---|
| Locality accuracy | Predicted locality is one of the accepted names (a spoken suffix like "Andheri **West**" counts if the base is accepted) |
| No invented locality | When the truth has no locality, we returned none (or flagged ambiguity) |
| State / city / district / PIN accuracy | Exact match on cases that specify the field |
| Component accuracy | house/unit/floor/plot/street/cross/lane/block/sector/phase/stage, normalised exact match |
| Exact match accuracy | Every specified field and component correct |
| Auto-resolution rate | Status `exact_match` or `resolved`, i.e. usable without asking |
| Precision when auto-resolved | Of auto-resolved cases, share with correct locality and state. **This is the "false confidence" guard.** |
| Incorrect correction rate | Of cases where a place name was approximately corrected, share where the result is wrong |
| Expected-status accuracy | Ambiguous/unresolved cases returned with that status |
| Calibration / ECE | Accuracy per confidence bin; expected calibration error |

## Results

### Held-out test set: first run (before looking at any test failures)

| Slice | N | Locality | State | City | PIN | Components | Exact | Auto-resolved | Precision auto | Wrong corrections | ECE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| All | 108 | 73.5% | 99.0% | 97.6% | 100% | 100% | 73.2% | 52.8% | **100%** | 41.7% (5/12) | 0.158 |
| Excluding coverage gaps | 94 | 82.6% | 98.9% | 97.1% | 100% | 100% | 80.9% | 60.6% | 100% | 30.0% | 0.111 |

### Fixes made after reading the first-run failures

All are general bugs or curation errors, not case-specific rules:

1. A name that is a district/city elsewhere but a locality inside the place the user also named is read as the locality ("Vaishali, Ghaziabad", "Bodh Gaya, Bihar").
2. Indic-script aliases in curated data were never indexed (their compact key was empty). They are now indexed via transliteration ("அடையார் சென்னை" → Adyar, Chennai).
3. "<Locality> Main Road" no longer swallows the locality ("Velacheri Main Road", "Rajpur Road").
4. "village X / tehsil Y / district Z" markers recognised; an unverifiable village name is preserved, not replaced by a different place.
5. Prefix candidates inside the named city ("Pattom" → "Pattom Palace").
6. A city written inside a matched name counts as mentioned ("Model Town Ludhiana").
7. Curation errors removed: Manipal, Bodh Gaya, Vrindavan and McLeod Ganj had wrongly been listed as aliases of their district cities. "Ooty → Udagamandalam" alias added.

### Held-out test set: after fixes

| Slice | N | Locality | State | City | PIN | Components | Exact | Auto-resolved | Precision auto | Wrong corrections | ECE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| All | 108 | 83.7% | 100% | 100% | 100% | 100% | 84.3% | 59.3% | **100%** | 15.4% (2/13) | 0.100 |
| Excluding coverage gaps | 94 | 94.2% | 100% | 100% | 100% | 100% | 93.6% | 68.1% | 100% | 0% (0/11) | 0.090 |
| Coverage gaps only | 14 | 8.3% (1/12) | 100% | 100% | — | — | — | 0% | — | — | — |

Ambiguous/unresolved cases flagged correctly: 4/5. The miss is "gandhi nagar", which resolves to Gandhinagar city (Gujarat): arguably valid, but the label expected `ambiguous`.

Calibration (test, after fixes):

| Confidence | N | Accuracy |
|---|---|---|
| 0.9-1.0 | 58 | 100% |
| 0.8-0.9 | 12 | 92% |
| 0.6-0.8 | 35 | 57% |
| < 0.6 | 3 | 100% |

The 0.6-0.8 band is mostly city-level results for coverage-gap places. They are correct at city level but miss the locality, and they always set `needs_confirmation`.

### Synthetic STT noise (500)

Locality 96.6%, state 98.2%, exact 96.4%, auto-resolved 79.0%, precision when auto-resolved 99.5%, wrong corrections 1.0% (2/209), ECE 0.066. Confidence ≥ 0.9: 99.6% accurate (n=274); 0.8-0.9: 98.4% (n=183).

### Reading these numbers honestly

- The test set is small (108) and AI-written. Treat the numbers as a sanity check of the pipeline, not as production accuracy.
- The after-fix numbers are optimistic: the fixes were found by reading those test failures.
- The most important property, **no wrong answer marked "resolved"**, held in both runs on the test set (0 of 57-64 auto-resolved cases wrong), and at 99.5% on the synthetic set.
- The biggest real-world gap is coverage, not matching: 13 of the 14 gap cases didn't resolve to a locality. Importing OpenStreetMap is the fix to evaluate next.
- Two remaining wrong corrections are both gap cases ("Hinjewadi" → "Shindewadi", "Koregaon Park" → "Karegaon"). Both were flagged `needs_confirmation`, and the voice agent would ask "Did you mean ...?".

## Next evaluation steps

1. 300-500 real, consented transcripts with confirmed addresses (stratified by city tier and language), split 50/50 into tune/test.
2. Re-run after an OSM import to measure the coverage gain.
3. Track the metrics per resolver version in CI.
