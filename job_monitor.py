"""
Job Monitor Agent
=================
Scrapes 58+ finance/fintech companies for PM job postings daily,
scores them against your resume using an LLM, deduplicates, and
writes new matches to a JSON database for your dashboard.

Setup:
  pip install requests gspread google-auth anthropic python-dotenv

Environment variables (.env):
  ANTHROPIC_API_KEY=...
  GOOGLE_SHEETS_ID=...         # from the URL of your sheet
  GOOGLE_CREDS_JSON=...        # path to service account JSON file
  ALERT_EMAIL=...              # your email (optional)
  SENDGRID_API_KEY=...         # for email alerts (optional)
"""

import os, json, time, hashlib, datetime, requests, subprocess
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── CONFIG ──────────────────────────────────────────────────────────────────

GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")
SHEETS_ID          = os.getenv("GOOGLE_SHEETS_ID", "15ijkCUdXizBrd5Ux7Y2J497aUu3yktCIRHuxPyxdeC0")
CREDS_PATH         = os.getenv("GOOGLE_CREDS_JSON", "creds.json")
ALERT_EMAIL        = os.getenv("ALERT_EMAIL", "")
SENDGRID_KEY       = os.getenv("SENDGRID_API_KEY", "")
SEEN_IDS_FILE      = Path("seen_job_ids.json")
MATCH_THRESHOLD    = 50   # lowered to 50 so you catch more potential matches
HEADERS            = {"User-Agent": "Mozilla/5.0 (compatible; JobMonitor/1.0)"}

# ─── YOUR RESUME SUMMARY ─────────────────────────────────────────────────────
# Paste a concise summary here. Keep it under ~400 words so it fits in context.

try:
    with open("resume.txt", "r") as f:
        RESUME_SUMMARY = f.read().strip()
except FileNotFoundError:
    RESUME_SUMMARY = """
    Name: Yeshwanth
    Role: Senior Product Manager / Associate PM candidate
    Experience: 4.5 years Senior PM at Quant Masters Technologies (ed-tech SaaS, 110K users);
                PM Intern at Zetwerk (post-acquisition manufacturing integration, 2025);
                MBA candidate Fordham Gabelli School of Business, graduating May 2026 (STEM, MIS track).

    Core skills:
    - Product strategy, roadmapping, OKRs, stakeholder management
    - Data: Power BI, Looker, SQL-level thinking, metrics frameworks
    - Technical: FastAPI, LangGraph, RAG systems, AI/ML product experience
    - Cross-functional leadership without authority, enterprise B2B, fintech/wealthtech interest

    Target roles:
    - Associate PM, Senior PM, Product Lead at finance or fintech companies
    - Especially: AI-powered products, digital wealth management, payments, B2B banking platforms
    - Companies: Goldman Sachs (Ayco), JPMorgan, Morgan Stanley, Visa, Mastercard, PayPal,
      Robinhood, Coinbase, Block, Capital One, Intuit, SoFi, Broadridge, FIS, and similar.

    Not interested in:
    - Pure engineering PM roles with no strategy component
    - Hardware, manufacturing, or non-financial verticals
    - Junior/associate-only roles requiring < 2 years experience
    """

# ─── COMPANY REGISTRY ────────────────────────────────────────────────────────

def load_companies():
    """Load the company registry from companies.json."""
    try:
        with open("companies.json", "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Error] Could not load companies.json: {e}")
        return []

COMPANIES = load_companies()


PM_KEYWORDS = [
    "product manager", "product management", "pm ", "associate pm",
    "senior pm", "principal pm", "staff pm", "vp product", "director product",
    "product lead", "group product manager", "head of product",
]

# ─── SCRAPER FUNCTIONS ───────────────────────────────────────────────────────

def scrape_greenhouse(token: str) -> list[dict]:
    """Returns list of {id, title, location, url, department}"""
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
        return [
            {
                "id":         f"gh_{j['id']}",
                "title":      j.get("title", ""),
                "location":   ", ".join(o.get("name","") for o in j.get("offices",[])),
                "url":        j.get("absolute_url", ""),
                "department": ", ".join(d.get("name","") for d in j.get("departments",[])),
                "description": j.get("content", "")[:600],
                "posted_date": j.get("updated_at", "")[:10],
            }
            for j in jobs
        ]
    except Exception as e:
        print(f"  [greenhouse/{token}] error: {e}")
        raise e  # Propagate to failures counter


def scrape_lever(token: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        jobs = r.json()
        return [
            {
                "id":         f"lv_{j['id']}",
                "title":      j.get("text", ""),
                "location":   j.get("categories", {}).get("location", ""),
                "url":        j.get("hostedUrl", ""),
                "department": j.get("categories", {}).get("department", ""),
                "description": j.get("descriptionPlain", "")[:600],
                "posted_date": str(datetime.datetime.fromtimestamp(j.get("createdAt", 0)/1000).date()) if j.get("createdAt") else "",
            }
            for j in jobs
        ]
    except Exception as e:
        print(f"  [lever/{token}] error: {e}")
        raise e  # Propagate to failures counter


def scrape_workday_search(token: str, query: str = "product manager") -> list[dict]:
    """
    Workday doesn't have a public unified API. This uses the
    search endpoint that many Workday career sites expose.
    You may need to find the exact subdomain + path for each company.
    Pattern: https://{token}.wd1.myworkdayjobs.com/wday/cxs/{token}/External/jobs
    """
    # Common Workday API endpoint patterns rotated by major banks
    for wd_domain in [f"{token}.wd1.myworkdayjobs.com", f"{token}.wd5.myworkdayjobs.com", f"{token}.wd3.myworkdayjobs.com", f"{token}.wd10.myworkdayjobs.com"]:
        url = f"https://{wd_domain}/wday/cxs/{token}/External/jobs"
        payload = {
            "appliedFacets": {},
            "limit": 20,
            "offset": 0,
            "searchText": query
        }
        try:
            r = requests.post(url, json=payload, headers={**HEADERS, "Content-Type": "application/json"}, timeout=15)
            if r.status_code == 200:
                data = r.json()
                jobs = data.get("jobPostings", [])
                return [
                    {
                        "id":         f"wd_{token}_{j.get('bulletFields',[''])[0]}_{hashlib.md5(j.get('title','').encode()).hexdigest()[:8]}",
                        "title":      j.get("title", ""),
                        "location":   j.get("locationsText", ""),
                        "url":        f"https://{wd_domain}/External/job/{j.get('externalPath','')}",
                        "department": j.get("jobFamilyGroup", ""),
                        "description": j.get("jobDescription", "")[:600] if "jobDescription" in j else "",
                        "posted_date": j.get("postedOn", "")[:10] if "postedOn" in j else "",
                    }
                    for j in jobs
                ]
        except Exception:
            pass  # try next domain variant
    
    # If we get here, all variants failed
    raise ConnectionError(f"Could not connect to any Workday portal for {token}")



def filter_pm_jobs(jobs: list[dict]) -> list[dict]:
    """Keep only jobs with PM-related keywords in the title."""
    results = []
    for j in jobs:
        title_lower = j["title"].lower()
        if any(kw in title_lower for kw in PM_KEYWORDS):
            results.append(j)
    return results


def fetch_jobs_for_company(company: dict) -> list[dict]:
    ats = company["ats"]
    token = company["token"]
    name = company["name"]
    print(f"  Fetching {name} ({ats})...")

    if ats == "greenhouse":
        jobs = scrape_greenhouse(token)
    elif ats == "lever":
        jobs = scrape_lever(token)
    elif ats == "workday_search":
        jobs = scrape_workday_search(token)
    else:
        jobs = []

    pm_jobs = filter_pm_jobs(jobs)
    for j in pm_jobs:
        j["company"] = name
    print(f"    → {len(pm_jobs)} PM jobs found (from {len(jobs)} total)")
    return pm_jobs


# ─── AI MATCHING ─────────────────────────────────────────────────────────────

def score_job_with_ai(job: dict) -> dict:
    """
    Calls Gemini to score the job against your resume.
    Returns the job dict enriched with: score, match_reason, apply_now.
    """
    import google.generativeai as genai
    import google.api_core.exceptions
    
    if not GEMINI_API_KEY:
        print("    [AI score error] GEMINI_API_KEY is not set.")
        job.update({"score": 0, "match_reason": "API Key missing", "apply_now": False,
                    "seniority": "unknown", "location_type": "unknown"})
        return job

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')

    prompt = f"""You are a career advisor. Score how well this job posting matches the candidate's profile.

CRITICAL RULE: The candidate operates at the 4-5 years of experience level. Reject (score < 40) ANY job that explicitly requires 7+ years of experience or is a Director/VP level role. However, "Senior Product Manager" roles requiring 3-5 years ARE A GOOD MATCH and should not be penalized. 

CANDIDATE PROFILE:
{RESUME_SUMMARY}

JOB POSTING:
Company: {job['company']}
Title: {job['title']}
Location: {job['location']}
Department: {job.get('department','')}
Description snippet: {job.get('description','')}
URL: {job['url']}

Return JSON only (no markdown):
{{
  "score": <integer 0-100>,
  "match_reason": "<one sentence why this is or isn't a good fit>",
  "apply_now": <true if score >= 70 and it's a strong match, else false>,
  "seniority": "<entry/associate/mid/senior/director>",
  "location_type": "<remote/hybrid/onsite/unknown>"
}}"""

    for attempt in range(3):
        try:
            response = model.generate_content(prompt)
            text = response.text.strip()
            if text.startswith("```json"): text = text[7:]
            elif text.startswith("```"): text = text[3:]
            if text.endswith("```"): text = text[:-3]
            text = text.strip()
            
            result = json.loads(text)
            job.update(result)
            return job
        except google.api_core.exceptions.ResourceExhausted:
            wait_time = (attempt + 1) * 30
            print(f"    [Rate Limit] 429 Resource exhausted. Waiting {wait_time}s...")
            time.sleep(wait_time)
        except Exception as e:
            print(f"    [AI score error] {e}")
            break
            
    return job
# ─── DEDUPLICATION ───────────────────────────────────────────────────────────

def load_seen_ids() -> set:
    if SEEN_IDS_FILE.exists():
        return set(json.loads(SEEN_IDS_FILE.read_text()))
    return set()

def save_seen_ids(seen: set):
    SEEN_IDS_FILE.write_text(json.dumps(list(seen)))


# ─── LOCAL JSON DATABASE ─────────────────────────────────────────────────────

def write_to_json(jobs: list[dict], scanned: int = 0, total: int = 0, status: str = "running", matches: int = 0, failures: int = 0):
    """Saves jobs and scan progress metadata to jobs.json."""
    try:
        from pathlib import Path
        import datetime
        data_file = Path("jobs.json")
        
        # Load existing data to maintain the full list
        if data_file.exists():
            try:
                raw = json.loads(data_file.read_text())
                # Handle both old list format and new dict format
                existing_jobs = raw.get("jobs", []) if isinstance(raw, dict) else raw
            except:
                existing_jobs = []
        else:
            existing_jobs = []
            
        existing_ids = {j["id"] for j in existing_jobs}
        today = datetime.datetime.now().isoformat()
        
        added = 0
        for j in jobs:
            if j["id"] not in existing_ids:
                j["fetch_date"] = today
                existing_jobs.append(j)
                added += 1
                
        # Sort so highest score & newest are at the top
        existing_jobs.sort(key=lambda x: (x.get("score", 0), x.get("fetch_date", "")), reverse=True)
        
        # Construct the new structured object
        output = {
            "metadata": {
                "last_updated": today,
                "scanned_count": scanned,
                "total_companies": total,
                "matches_found": matches,
                "technical_failures": failures,
                "status": status
            },
            "jobs": existing_jobs
        }
        
        data_file.write_text(json.dumps(output, indent=2))
        
        # Incremental Git Push to update the dashboard "live"
        try:
            subprocess.run(["git", "add", "jobs.json", "seen_job_ids.json"], check=True, capture_output=True)
            commit_msg = f"Live Update: {scanned}/{total} companies scanned"
            subprocess.run(["git", "commit", "-m", commit_msg], check=True, capture_output=True)
            subprocess.run(["git", "push"], check=True, capture_output=True)
            print(f"  Live update pushed to GitHub ({scanned}/{total})")
        except Exception as git_err:
            pass # Silent failure if git is busy, results will sync on next loop

        if added > 0:
            print(f"✓ Appended {added} new jobs to jobs.json ({scanned}/{total})")
        else:
            print(f"  Progress update: {scanned}/{total} companies scanned")
    except Exception as e:
        print(f"[JSON error] {e}")


# ─── EMAIL ALERT ─────────────────────────────────────────────────────────────

def send_email_alert(top_jobs: list[dict]):
    """Send email via SendGrid for apply_now=True jobs."""
    if not top_jobs or not SENDGRID_KEY or not ALERT_EMAIL:
        return
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail
        sg = sendgrid.SendGridAPIClient(SENDGRID_KEY)
        body = "\n\n".join(
            f"{j['company']} — {j['title']}\nScore: {j['score']}/100\n{j['match_reason']}\n{j['url']}"
            for j in top_jobs
        )
        msg = Mail(
            from_email="jobmonitor@yourdomain.com",
            to_emails=ALERT_EMAIL,
            subject=f"[Job Monitor] {len(top_jobs)} new PM jobs to apply for",
            plain_text_content=body,
        )
        sg.send(msg)
        print(f"✓ Email alert sent for {len(top_jobs)} top jobs")
    except Exception as e:
        print(f"[Email error] {e}")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"Job Monitor run: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    seen_ids  = load_seen_ids()
    all_new   = []
    apply_now = []
    total_companies = len(COMPANIES)
    matches_found = 0
    technical_failures = 0

    for i, company in enumerate(COMPANIES, 1):
        print(f"  Scanning {company['name']} ({i}/{total_companies})...")
        
        # Immediate Progress Pulse
        write_to_json([], scanned=i, total=total_companies, matches=matches_found, failures=technical_failures)
        
        try:
            jobs = fetch_jobs_for_company(company)
            new_jobs = [j for j in jobs if j["id"] not in seen_ids]
            
            company_has_match = False
            if new_jobs:
                print(f"  Scoring {len(new_jobs)} new jobs for {company['name']}...")
                for job in new_jobs:
                    scored = score_job_with_ai(job)
                    seen_ids.add(job["id"])
                    if scored.get("score", 0) >= MATCH_THRESHOLD:
                        all_new.append(scored)
                        company_has_match = True
                        if scored.get("apply_now"):
                            apply_now.append(scored)
                        
                        # Streaming Update
                        write_to_json(all_new, scanned=i, total=total_companies, matches=matches_found + (1 if company_has_match else 0), failures=technical_failures)
                        
                    time.sleep(5.0) 

            if company_has_match:
                matches_found += 1

            # Final company update
            write_to_json(all_new, scanned=i, total=total_companies, matches=matches_found, failures=technical_failures)
            
            # Persist seen IDs progressively to avoid rescrapes
            save_seen_ids(seen_ids)

        except Exception as e:
            print(f"  [Error processing {company['name']}] {e}")
            technical_failures += 1
            write_to_json(all_new, scanned=i, total=total_companies, matches=matches_found, failures=technical_failures)


    # Sort by score descending
    all_new.sort(key=lambda x: x.get("score", 0), reverse=True)
    apply_now.sort(key=lambda x: x.get("score", 0), reverse=True)

    print(f"\n{'='*60}")
    print(f"Run complete. {len(all_new)} new matches (score ≥ {MATCH_THRESHOLD})")
    print(f"Apply now: {len(apply_now)} jobs")
    print(f"{'='*60}\n")

    # Print top 10 to console
    for j in all_new[:10]:
        print(f"  [{j['score']:3d}] {j['company']} — {j['title']}")
        print(f"        {j['match_reason']}")
        print(f"        {j['url']}\n")

    # Final signal that run is done
    write_to_json([], scanned=total_companies, total=total_companies, status="complete")
    send_email_alert(apply_now)

    print("✓ Run fully complete and securely synchronized")


if __name__ == "__main__":
    main()
