# Evaluation results

Resolver run on 2026-11-23. LLM disabled (deterministic pipeline only). See docs/evaluation.md for definitions and caveats.

### dev.jsonl

| Slice | N | Locality | State | City | PIN | Components | Exact match | Auto-resolved | Precision when auto-resolved | Incorrect corrections | Ambiguous/unresolved flagged | ECE |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| overall | 25 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 60.0% | 100.0% | 0.0% | 100.0% | 0.216 |
| excluding coverage gaps | 25 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 60.0% | 100.0% | 0.0% | 100.0% | 0.216 |

Calibration (overall):

| Confidence | N | Avg confidence | Accuracy |
|---|---|---|---|
| 0.0-0.4 | 2 | 0.00 | 100% |
| 0.4-0.6 | 1 | 0.56 | 100% |
| 0.6-0.8 | 4 | 0.68 | 100% |
| 0.8-0.9 | 8 | 0.85 | 100% |
| 0.9-1.0 | 10 | 0.95 | 100% |

Failures (0):


### test.jsonl

| Slice | N | Locality | State | City | PIN | Components | Exact match | Auto-resolved | Precision when auto-resolved | Incorrect corrections | Ambiguous/unresolved flagged | ECE |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| overall | 108 | 83.7% | 100.0% | 100.0% | 100.0% | 100.0% | 84.3% | 59.3% | 100.0% | 15.4% | 80.0% | 0.100 |
| excluding coverage gaps | 94 | 94.2% | 100.0% | 100.0% | 100.0% | 100.0% | 93.6% | 68.1% | 100.0% | 0.0% | 80.0% | 0.090 |
| coverage gaps only | 14 | 8.3% | 100.0% | 100.0% | - | - | 21.4% | 0.0% | - | 100.0% | - | 0.511 |

Calibration (overall):

| Confidence | N | Avg confidence | Accuracy |
|---|---|---|---|
| 0.0-0.4 | 2 | 0.00 | 100% |
| 0.4-0.6 | 1 | 0.47 | 100% |
| 0.6-0.8 | 35 | 0.71 | 57% |
| 0.8-0.9 | 12 | 0.85 | 92% |
| 0.9-1.0 | 58 | 0.95 | 100% |

Failures (17):

- `t006` 'mera ghar hebbal mein hai, bangalore' -> 'Bengaluru, Karnataka, India' [partially_resolved 0.72] failed: locality
- `t021` 'dharampeth nagpur' -> 'Dharampeth, Nagpur, Maharashtra, India' [partially_resolved 0.72] failed: locality (coverage gap)
- `t045` 'secunderabad' -> 'Secunderabad, Telangana, India' [partially_resolved 0.72] failed: locality
- `t050` 'park street calcutta' -> 'Kolkata, West Bengal, India' [partially_resolved 0.72] failed: locality
- `t058` 'c scheme jaipur' -> 'C Scheme, Jaipur, Rajasthan, India' [partially_resolved 0.72] failed: locality (coverage gap)
- `t060` 'arera colony bhopal' -> 'Bhopal, Madhya Pradesh, India' [partially_resolved 0.72] failed: locality (coverage gap)
- `t062` 'hazratganj lucknow' -> 'Lucknow, Uttar Pradesh, India' [partially_resolved 0.72] failed: locality (coverage gap)
- `t064` 'civil lines allahabad' -> 'Prayagraj, Uttar Pradesh, India' [partially_resolved 0.72] failed: locality
- `t068` 'sector 17 chandigarh' -> 'Sector 17, Chandigarh, India' [partially_resolved 0.72] failed: locality (coverage gap)
- `t070` 'ranjit avenue amritsar' -> 'Ranjit Avenue, Amritsar, Punjab, India' [partially_resolved 0.72] failed: locality (coverage gap)
- `t071` 'boring road patna' -> 'Boring Road, Patna, Bihar, India' [partially_resolved 0.72] failed: locality (coverage gap)
- `t074` 'panjim goa' -> 'Panaji, Goa, India' [partially_resolved 0.72] failed: locality
- `t098` 'gandhi nagar' -> 'Gandhinagar, Gujarat, India' [partially_resolved 0.72] failed: expected_status
- `t105` 'btm layout 2nd stage bangalore' -> '2nd Stage, Btm Layout, Bengaluru, Karnataka, India' [partially_resolved 0.72] failed: locality (coverage gap)
- `t106` 'hinjewadi phase 1 pune' -> 'Phase 1, Shindewadi, Pune, Maharashtra, India' [partially_resolved 0.811] failed: locality (coverage gap)
- `t107` 'koregaon park pune' -> 'Karegaon, Pune, Maharashtra, India' [partially_resolved 0.793] failed: locality (coverage gap)
- `t108` 'indirapuram ghaziabad' -> 'Ghaziabad, Uttar Pradesh, India' [partially_resolved 0.72] failed: locality (coverage gap)

### synthetic_stt.jsonl

| Slice | N | Locality | State | City | PIN | Components | Exact match | Auto-resolved | Precision when auto-resolved | Incorrect corrections | Ambiguous/unresolved flagged | ECE |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| overall | 500 | 96.6% | 98.2% | - | 100.0% | - | 96.4% | 79.0% | 99.5% | 1.0% | - | 0.066 |
| excluding coverage gaps | 500 | 96.6% | 98.2% | - | 100.0% | - | 96.4% | 79.0% | 99.5% | 1.0% | - | 0.066 |

Calibration (overall):

| Confidence | N | Avg confidence | Accuracy |
|---|---|---|---|
| 0.0-0.4 | 3 | 0.00 | 0% |
| 0.4-0.6 | 10 | 0.56 | 70% |
| 0.6-0.8 | 30 | 0.73 | 73% |
| 0.8-0.9 | 183 | 0.86 | 98% |
| 0.9-1.0 | 274 | 0.96 | 100% |

Failures (18):

- `s0018` 'flat 2b, kavaratti' -> 'Flat 2B, Kavaratti, Lakshadweep, India' [partially_resolved 0.72] failed: locality
- `s0074` 'flat 2b, wallong, 792104' -> 'Flat 2B, Wallong, Anjaw District, Arunachal Pradesh 792104, India' [exact_match 0.99] failed: locality
- `s0080` '47, khera kalan' -> 'No. 47, Khera Kalan, India' [unresolved 0.0] failed: locality, state, district
- `s0084` 'dahd, bilaspur' -> 'Bilaspur (Chhattisgarh), Chhattisgarh, India' [ambiguous 0.536] failed: locality, state
- `s0120` 'manali, kullu' -> 'Manali, Himachal Pradesh, India' [partially_resolved 0.72] failed: locality
- `s0215` '52, jashpur' -> 'No. 52, Jashpur District, Chhattisgarh, India' [partially_resolved 0.66] failed: locality, state, district
- `s0221` 'chandigarh' -> 'Chandigarh, India' [partially_resolved 0.72] failed: locality
- `s0256` 'house no 268, tayap, aurangabad' -> 'No. 268, Chhatrapati Sambhajinagar, Maharashtra, India' [ambiguous 0.576] failed: locality, state
- `s0257` 'sector 11 candigarh, chandigarh' -> 'Sector 11, Chandigarh, Chandigarh 160017, India' [resolved 0.875] failed: locality
- `s0278` 'house no 99, areeyur, six zero five one zero two' -> 'No. 99, Viluppuram District, Tamil Nadu 605102, India' [partially_resolved 0.88] failed: locality, state, district
- `s0283` '20, konijerla' -> 'No. 20, Konijerla, NTR District, Andhra Pradesh, India' [ambiguous 0.636] failed: state, district
- `s0331` 'flat 2b, silvssa, dadra and nagar haveli dadra and nagar haveli and daman and diu' -> 'Flat 2B, Silvassa, Dadra and Nagar Haveli and Daman and Diu, India' [partially_resolved 0.72] failed: locality
- `s0414` 'house no 170, ameeni' -> 'No. 170, Amni, Khagaria District, Bihar, India' [ambiguous 0.551] failed: locality, state, district
- `s0429` '73, eendara' -> 'No. 73, Eendara, India' [unresolved 0.0] failed: locality, state, district
- `s0441` '22, panaji, north goa' -> 'No. 22, Panaji, Goa, India' [partially_resolved 0.72] failed: locality
- `s0472` 'house no 84, mirzapr, bhagalpur bihar' -> 'No. 84, Bhagalpur, Bihar, India' [partially_resolved 0.72] failed: locality
- `s0478` 'akla, three six five four three zero' -> 'Amreli District, Gujarat 365430, India' [partially_resolved 0.88] failed: locality
- `s0482` '11, gargee' -> 'No. 11, Gargee, India' [unresolved 0.0] failed: locality, state, district
