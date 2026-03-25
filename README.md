# Job Monitor Agent
Daily PM job tracker for 63+ US finance/fintech companies.
Scrapes ATS boards → AI-scores against your resume → writes to Google Sheets → emails top matches.

---

## Setup (30 minutes)

### Step 1 — Fork/clone this repo to your GitHub

### Step 2 — Google Sheets + Service Account
1. Go to console.cloud.google.com → create a project
2. Enable "Google Sheets API"
3. Create a Service Account → download the JSON key → save as `creds.json`
4. Create a new Google Sheet → copy the ID from the URL
   (e.g. `https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit`)
5. Share the sheet with the service account email (from the JSON file) — give Editor access

### Step 3 — GitHub Secrets
In your repo → Settings → Secrets and variables → Actions, add:

| Secret Name         | Value                                      |
|---------------------|--------------------------------------------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key                     |
| `GOOGLE_SHEETS_ID`  | The ID from your Google Sheet URL          |
| `GOOGLE_CREDS_JSON` | The full contents of your creds.json file  |
| `ALERT_EMAIL`       | Your email address (for daily alerts)      |
| `SENDGRID_API_KEY`  | SendGrid key (free tier works fine)        |

### Step 4 — Update your resume in job_monitor.py
Edit the `RESUME_SUMMARY` string at the top of `job_monitor.py` to reflect your actual experience.

### Step 5 — Test run
Go to GitHub Actions → "Daily Job Monitor" → "Run workflow" to trigger manually.

---

## How it works

1. Runs every day at 7 AM EST via GitHub Actions (free, no server needed)
2. Fetches PM job postings from Greenhouse, Lever, and Workday ATS boards
3. Filters to PM-title jobs only
4. Deduplicates (skips jobs already seen)
5. Scores each new job 0–100 against your resume using Claude
6. Writes all matches (score ≥ 70) to your Google Sheet
7. Emails you a summary of "apply now" jobs (score ≥ 70 + strong fit)

---

## Google Sheet columns
| Date Found | Company | Title | Location | Score | Match Reason | Seniority | Location Type | Apply Now? | URL | Department |

---

## Adjusting the threshold
In `job_monitor.py`, change `MATCH_THRESHOLD = 70` to any value (0-100).
Lower = more results, higher = fewer but stronger matches.

---

## Cost estimate
- Claude API: ~$0.01–0.05 per run depending on how many new jobs appear
- GitHub Actions: free (within free tier limits)
- SendGrid: free (100 emails/day free tier)
- Total: essentially free to run daily

---

## Adding more companies
Add entries to the `COMPANIES` list in `job_monitor.py`:
```python
{"name": "Your Company", "ats": "greenhouse", "token": "their-greenhouse-slug"},
```
Find the Greenhouse slug by visiting: https://boards.greenhouse.io/COMPANY_SLUG
