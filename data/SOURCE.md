# Data sources

## `duty_mos_noise.xlsx`

**File:** `data/duty_mos_noise.xlsx`
**Source:** VA Veterans Benefits Administration — Fast Letter 10-35 (Sept 2010),
"Modifying the Development Process in Claims for Hearing Loss and/or Tinnitus."
Attachment: Duty MOS Noise Exposure Listing.
**Retrieval URL:** https://vaclaimsinsider.com/wp-content/uploads/2024/08/Official-List-of-Duty-MOS-Noise-Exposure-Levels.xlsx
**Alternate mirror:** https://www.tn.gov/content/dam/tn/veteranservices/learning/vso-tools/tools/Duty%20MOS%20Noise%20Exposure%20Levels.xls
**Retrieved:** 2026-05-22
**License:** US Government work, public domain (17 USC §105).

The spreadsheet is the authoritative VBA reference for adjudicating in-service
acoustic-trauma claims. It enumerates every Job Code (MOS / Navy Rating / AFSC)
across every branch and assigns each one a probability of in-service noise
exposure: Highly Probable, Moderate, or Low.

Used by `src/va_agent/ingestion/jobcodes.py` to build the authoritative
`:CFR:JobCode` spine plus `:NOISE_EXPOSURE` edges to the `hearing` Anatomy.

## `mos_risk.yaml`

**File:** `data/mos_risk.yaml`
**Source:** Hand-curated overlay (this repo). Entries draw on publicly available
duty descriptions: DA PAM 611-21 (Army), MCO 1200.18 (Marine MOS Manual), Navy
Enlisted Classification (NEC) Manual, AFECD (Air Force Enlisted Classification
Directory), and well-known duty traits of the cited jobs (lifting, prolonged
kneeling, vibration, sustained heavy load carriage, etc).
**License:** This repo's license.

These are *inferences* about musculoskeletal/other non-acoustic risks, not
legally authoritative determinations. Every entry is tagged `confidence: medium`
to signal that to downstream consumers. The overlay can never invent new
JobCodes — it MATCHes existing ones from the spine or refuses to apply.
