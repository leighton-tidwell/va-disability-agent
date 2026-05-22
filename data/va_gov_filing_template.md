# Filing your Claim on va.gov

This document walks through filing the Claim you just prepared on va.gov.
Keep it open in a tab while you go through the online form.

<!--
This template is loaded by `va_agent.output.closing` and rendered with
`{{placeholder}}` substitution. URLs verified against va.gov as of 2025/2026;
the disability claim form (21-526EZ) page and the sign-in / claim-status pages
have been stable for several years. If va.gov restructures, update the URLs
here and re-run the closing generator — no code changes needed.
-->

## 1. Open the disability claim form

Go to:

  https://www.va.gov/disability/file-disability-claim-form-21-526ez/

This is the official va.gov landing page for VA Form 21-526EZ —
"Application for Disability Compensation and Related Compensation Benefits."
Click **Start your application**.

## 2. Sign in

You will be prompted to sign in via one of:

- **Login.gov** (recommended for new users)
- **ID.me**
- **My HealtheVet**
- **DS Logon**

If you don't already have an account, the sign-in page walks you through
creating one. Sign-in hub:

  https://www.va.gov/sign-in/

## 3. Confirm your personal info

va.gov pre-fills your name, date of birth, Social Security number, and
service history from VA records. Confirm everything is correct before
moving on. If anything is wrong, fix it now — corrections later in the
form are harder.

## 4. Add your condition(s)

You will reach a step labeled something like **"What conditions are you
claiming?"**. For each Claimed Condition we prepared, click **Add a new
condition** (or **Add another condition** for the second and beyond).

You are filing **{{n_conditions}}** Claimed Condition(s) in this submission:

{{condition_list}}

For each condition you add:

- **Condition name** — type the plain-language name of the condition.
  Example: "{{condition_title_example}}". You do **not** need to enter the
  Diagnostic Code number; the VA rater assigns that.
- **Description / "What happened or worsened in service?"** — paste the
  **Lay Statement** from the corresponding `dc-NNNN.md` file in this
  folder. The Lay Statement is already written in CFR vocabulary and uses
  only facts you confirmed during the session.

  Example excerpt from one of your Lay Statements:

  > {{lay_statement_excerpt}}

## 5. Upload supporting Evidence (optional but recommended)

For each Claimed Condition, the per-condition file in this folder lists
the Evidence to gather:

- Service Treatment Record (STR) excerpts
- Private Medical Records from the last 12 months
- Buddy Statements

Upload whatever you have. You can also add Evidence later via the
"Submit Evidence" workflow — filing now and adding Evidence after is fine.

## 6. Review and submit

va.gov shows a final review page. Check that every condition you intended
to file is listed, then submit. You will see a confirmation page with a
**claim reference number** — save it.

## 7. What happens next

- The VA acknowledges the Claim within a few days.
- You will be scheduled for one or more **Compensation & Pension (C&P)
  exams**, typically within **30–60 days** of filing. The C&P exam is
  the single most important step.
- **Bring the `dc-NNNN.md` file for each condition to its C&P exam.**
  The "C&P Exam Preparation" section tells you what the examiner will
  measure and what to describe.

## 8. Track your Claim

After submission, track status at:

  https://www.va.gov/claim-or-appeal-status/

You can also call the VA at **1-800-827-1000** for updates.

---

*Drafted for Claim `{{claim_id}}` on {{generated_at}}.*
