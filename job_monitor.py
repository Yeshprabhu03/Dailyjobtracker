"""
Job Monitor Agent — Fixed Version
===================================
Key fixes in this version:
  1. Workday scraper now uses per-company "path" field from companies.json
  2. Added SmartRecruiters scraper (for Visa, Intuit)
  3. Added JPMorgan scraper (careers.jpmorgan.com JSON API)
  4. Added Goldman Sachs scraper (higher.gs.com)
  5. Expanded PM_KEYWORDS to catch more titles
  6. Fixed LPL, KKR, Apollo, Ares greenhouse slugs
  7. Node.js deprecation warning fix (see workflow file)
"""

import os, json, time, re, hashlib, datetime, requests, subprocess
from pathlib import Path
from urllib.parse import urljoin
from dotenv import load_dotenv
from bs4 import BeautifulSoup

load_dotenv()

GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")
SHEETS_ID       = os.getenv("GOOGLE_SHEETS_ID", "")
CREDS_PATH      = os.getenv("GOOGLE_CREDS_JSON", "creds.json")
ALERT_EMAIL     = os.getenv("ALERT_EMAIL", "")
SENDGRID_KEY    = os.getenv("SENDGRID_API_KEY", "")
SEEN_IDS_FILE   = Path("seen_job_ids.json")
MATCH_THRESHOLD = 50
HEADERS         = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
}

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
Core skills: Product strategy, roadmapping, OKRs, stakeholder management, Power BI, Looker,
             SQL, FastAPI, LangGraph, RAG systems, AI/ML product experience, B2B fintech/wealthtech.
Target roles: Associate PM, Senior PM, Product Lead at finance or fintech companies.
Especially: AI products, digital wealth management, payments, B2B banking platforms.
Not interested in: Pure engineering PM roles, hardware, or junior roles requiring <2 years.
"""

def load_companies():
    try:
        with open("companies.json", "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Error] Could not load companies.json: {e}")
        return []

COMPANIES = load_companies()

# Expanded keywords — catches "product associate", "digital PM", etc.
PM_KEYWORDS = [
    "product manager", "product management", " pm ", "associate pm",
    "senior pm", "principal pm", "staff pm", "vp product", "director of product",
    "director, product", "product lead", "group product manager", "head of product",
    "product owner", "product associate", "product analyst", "digital product",
    "vp, product", "product vice president", "chief product",
]

# ─── SCRAPERS ────────────────────────────────────────────────────────────────

def scrape_greenhouse(token: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
        return [
            {
                "id":          f"gh_{j['id']}",
                "title":       j.get("title", ""),
                "location":    ", ".join(o.get("name", "") for o in j.get("offices", [])),
                "url":         j.get("absolute_url", ""),
                "department":  ", ".join(d.get("name", "") for d in j.get("departments", [])),
                "description": j.get("content", "")[:600],
                "posted_date": j.get("updated_at", "")[:10],
            }
            for j in jobs
        ]
    except Exception as e:
        print(f"  [greenhouse/{token}] error: {e}")
        raise


def scrape_workday_search(token: str, path: str = "External",
                          query: str = "product manager") -> list[dict]:
    """
    Try multiple Workday domain variants with the correct path per company.
    Pattern: https://{token}.wd1.myworkdayjobs.com/wday/cxs/{token}/External/jobs
    """
    # Build paths list: company-specific path first, then generic fallbacks
    generic_paths = ["External", "Careers", "Jobs", "external"]
    paths_to_try = ([path] if path not in generic_paths else []) + generic_paths

    for wd_domain in [f"{token}.wd1.myworkdayjobs.com", f"{token}.wd5.myworkdayjobs.com", f"{token}.wd3.myworkdayjobs.com", f"{token}.wd10.myworkdayjobs.com"]:
        for p in paths_to_try:
            url = f"https://{wd_domain}/wday/cxs/{token}/{p}/jobs"
            payload = {
                "appliedFacets": {},
                "limit": 20,
                "offset": 0,
                "searchText": query
            }
            try:
                r = requests.post(url, json=payload, headers={**HEADERS, "Content-Type": "application/json", "Accept": "application/json"}, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    jobs = data.get("jobPostings", [])
                    return [
                        {
                            "id":         f"wd_{token}_{j.get('bulletFields',[''])[0]}_{hashlib.md5(j.get('title','').encode()).hexdigest()[:8]}",
                            "title":      j.get("title", ""),
                            "location":   j.get("locationsText", ""),
                            "url":        f"https://{wd_domain}/{p}/job/{j.get('externalPath','')}",
                            "department": j.get("jobFamilyGroup", ""),
                            "description": j.get("jobDescription", "")[:600] if "jobDescription" in j else "",
                            "posted_date": j.get("postedOn", "")[:10] if "postedOn" in j else "",
                        }
                        for j in jobs
                    ]
            except Exception:
                pass  # try next combination
    raise ConnectionError(f"Could not connect to any Workday portal for {token}")


def scrape_smartrecruiters(token: str, query: str = "product manager") -> list[dict]:
    """
    SmartRecruiters public job search API.
    Used by Visa, Intuit, and others.
    """
    url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    params = {
        "q":      query,
        "limit":  100,
        "offset": 0,
        "country": "us",  # US only
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        content = r.json()
        jobs = content.get("content", [])
        return [
            {
                "id":          f"sr_{j['id']}",
                "title":       j.get("name", ""),
                "location":    f"{j.get('location', {}).get('city', '')}, "
                               f"{j.get('location', {}).get('region', '')}".strip(", "),
                "url":         f"https://jobs.smartrecruiters.com/{token}/{j['id']}",
                "department":  j.get("department", {}).get("label", "") if j.get("department") else "",
                "description": j.get("jobAd", {}).get("sections", {}).get("jobDescription", {}).get("text", "")[:600]
                               if j.get("jobAd") else "",
                "posted_date": j.get("releasedDate", "")[:10],
            }
            for j in jobs
        ]
    except Exception as e:
        print(f"  [smartrecruiters/{token}] error: {e}")
        raise


def scrape_workday_search(token: str, path: str = "External",
                          query: str = "product manager") -> list[dict]:
    """
    Try multiple Workday domain variants with the correct path per company.
    Pattern: https://{token}.wd1.myworkdayjobs.com/wday/cxs/{token}/{path}/jobs
    """
    # Try company-specific path first, then common fallbacks
    paths_to_try = [path] if path else []
    for p in ["External", "Careers", "Jobs", "external"]:
        if p not in paths_to_try:
            paths_to_try.append(p)

    for wd_domain in [f"{token}.wd1.myworkdayjobs.com", f"{token}.wd5.myworkdayjobs.com",
                      f"{token}.wd3.myworkdayjobs.com", f"{token}.wd10.myworkdayjobs.com"]:
        for p in paths_to_try:
            url = f"https://{wd_domain}/wday/cxs/{token}/{p}/jobs"
            payload = {
                "appliedFacets": {},
                "limit": 20,
                "offset": 0,
                "searchText": query
            }
            try:
                r = requests.post(url, json=payload,
                                  headers={**HEADERS, "Content-Type": "application/json",
                                           "Accept": "application/json"},
                                  timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    jobs = data.get("jobPostings", [])
                    return [
                        {
                            "id":         f"wd_{token}_{j.get('bulletFields',[''])[0]}_{hashlib.md5(j.get('title','').encode()).hexdigest()[:8]}",
                            "title":      j.get("title", ""),
                            "location":   j.get("locationsText", ""),
                            "url":        f"https://{wd_domain}/{p}/job/{j.get('externalPath','')}",
                            "department": j.get("jobFamilyGroup", ""),
                            "description": j.get("jobDescription", "")[:600] if "jobDescription" in j else "",
                            "posted_date": j.get("postedOn", "")[:10] if "postedOn" in j else "",
                        }
                        for j in jobs
                    ]
            except Exception:
                pass  # try next combination

    raise ConnectionError(f"Could not connect to any Workday portal for {token}")


def scrape_goldman(query: str = "product manager") -> list[dict]:
    """Goldman Sachs careers — tries JSON API then falls back to HTML scrape."""
    url = "https://higher.gs.com/api/jobs/search"
    params = {"q": query, "page": 1, "pageSize": 50}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        jobs = data.get("roles", data.get("jobs", data.get("results", [])))
        return [
            {
                "id":          f"gs_{j.get('id', hashlib.md5(j.get('title','').encode()).hexdigest()[:8])}",
                "title":       j.get("title", j.get("name", "")),
                "location":    j.get("location", {}).get("name", "") if isinstance(j.get("location"), dict) else j.get("location", ""),
                "url":         f"https://higher.gs.com/roles/{j.get('id', '')}",
                "department":  j.get("division", j.get("department", "")),
                "description": j.get("description", "")[:600],
                "posted_date": j.get("datePosted", j.get("posted_date", ""))[:10] if j.get("datePosted") or j.get("posted_date") else "",
            }
            for j in jobs
        ]
    except Exception as e:
        print(f"  [goldman] error: {e}")
        try:
            r2 = requests.get(
                f"https://higher.gs.com/results?search={query.replace(' ', '+')}&page=1",
                headers=HEADERS, timeout=15
            )
            return scrape_career_link_html(r2.text, "Goldman Sachs", "https://higher.gs.com/results")
        except Exception as e2:
            print(f"  [goldman fallback] error: {e2}")
            raise e


def scrape_smartrecruiters(token: str, query: str = "product manager") -> list[dict]:
    """SmartRecruiters public job search API. Used by Visa, Intuit, and others."""
    url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    params = {"q": query, "limit": 100, "offset": 0, "country": "us"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        jobs = r.json().get("content", [])
        return [
            {
                "id":          f"sr_{j['id']}",
                "title":       j.get("name", ""),
                "location":    f"{j.get('location', {}).get('city', '')}, "
                               f"{j.get('location', {}).get('region', '')}".strip(", "),
                "url":         f"https://jobs.smartrecruiters.com/{token}/{j['id']}",
                "department":  j.get("department", {}).get("label", "") if j.get("department") else "",
                "description": j.get("jobAd", {}).get("sections", {}).get("jobDescription", {}).get("text", "")[:600]
                               if j.get("jobAd") else "",
                "posted_date": j.get("releasedDate", "")[:10],
            }
            for j in jobs
        ]
    except Exception as e:
        print(f"  [smartrecruiters/{token}] error: {e}")
        raise


def scrape_eightfold(token: str, query: str = "product manager") -> list[dict]:
    """Scraper for Eightfold AI (Amex, PayPal)."""
    domain_map = {"aexp": "aexp.com", "paypal": "paypal.com"}
    domain = domain_map.get(token, f"{token}.com")
    url = f"https://{token}.eightfold.ai/api/apply/v2/jobs"
    params = {"domain": domain, "query": query, "sort_by": "relevance"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        jobs = r.json().get("positions", [])
        return [
            {
                "id":          f"ef_{token}_{j['id']}",
                "title":       j.get("name", ""),
                "location":    j.get("location", ""),
                "url":         f"https://{token}.eightfold.ai/careers?jobId={j['id']}",
                "department":  j.get("department", ""),
                "description": j.get("department", ""),
                "posted_date": datetime.datetime.now().strftime("%Y-%m-%d"),
            }
            for j in jobs
        ]
    except Exception as e:
        print(f"  [eightfold/{token}] error: {e}")
        raise


def scrape_jpmorgan(query: str = "product manager") -> list[dict]:
    """JPMorgan Chase careers JSON API."""
    url = "https://careers.jpmorgan.com/api/jobs/search"
    params = {"q": query, "location": "United States", "page": 1, "pageSize": 50, "lang": "en"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        jobs = data.get("jobs", data.get("results", []))
        return [
            {
                "id":          f"jpm_{j.get('jobId', hashlib.md5(j.get('title','').encode()).hexdigest()[:8])}",
                "title":       j.get("title", ""),
                "location":    j.get("location", {}).get("cityStateCountry", "") if isinstance(j.get("location"), dict) else j.get("location", ""),
                "url":         f"https://careers.jpmorgan.com/us/en/jobs/{j.get('jobId', '')}",
                "department":  j.get("businessArea", ""),
                "description": j.get("jobDescription", "")[:600] if j.get("jobDescription") else "",
                "posted_date": j.get("postDate", "")[:10],
            }
            for j in jobs
        ]
    except Exception as e:
        print(f"  [jpmorgan] error: {e}")
        raise


def scrape_goldman(query: str = "product manager") -> list[dict]:
    """Goldman Sachs careers at higher.gs.com."""
    url = "https://higher.gs.com/api/jobs/search"
    params = {"q": query, "page": 1, "pageSize": 50}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        jobs = data.get("roles", data.get("jobs", data.get("results", [])))
        return [
            {
                "id":          f"gs_{j.get('id', hashlib.md5(j.get('title','').encode()).hexdigest()[:8])}",
                "title":       j.get("title", j.get("name", "")),
                "location":    j.get("location", {}).get("name", "") if isinstance(j.get("location"), dict) else j.get("location", ""),
                "url":         f"https://higher.gs.com/roles/{j.get('id', '')}",
                "department":  j.get("division", j.get("department", "")),
                "description": j.get("description", "")[:600],
                "posted_date": (j.get("datePosted") or j.get("posted_date", ""))[:10],
            }
            for j in jobs
        ]
    except Exception as e:
        print(f"  [goldman] error: {e}")
        raise


def scrape_oracle_cloud(token: str, query: str = "product manager") -> list[dict]:
    """Specialized scraper for Oracle Cloud Recruiting (used by JPM)."""
    # Pattern: https://{token}.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingJobPostings
    # For JPM: jpmc
    # Token can be a full hostname (e.g. hcgn.fa.us2)
    base_host = f"{token}.fa.oraclecloud.com" if "." not in token else f"{token}.oraclecloud.com"
    url = f"https://{base_host}/hcmRestApi/resources/latest/recruitingJobPostings"
    params = {
        "limit":  50,
        "q":      f"title LIKE '%{query}%' OR unformattedDescription LIKE '%{query}%'",
        "expand": "externalJobPostings",
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        content = r.json()
        jobs = content.get("items", [])
        return [
            {
                "id":          f"oc_{j['Id']}",
                "title":       j.get("Title", ""),
                "location":    j.get("PrimaryLocation", ""),
                "url":         f"https://{base_host}/hcmRestApi/resources/latest/recruitingJobPostings/{j['Id']}",
                "department":  j.get("Organization", ""),
                "description": j.get("Description", "")[:600],
                "posted_date": j.get("PostedDate", "")[:10],
            }
            for j in jobs
        ]
    except Exception as e:
        print(f"  [oracle_cloud/{token}] error: {e}")
        raise


def scrape_eightfold(token: str, query: str = "product manager") -> list[dict]:
    """Scraper for Eightfold AI (Amex, PayPal)."""
    # Token is the subdomain (e.g. 'aexp')
    # Domain is usually {token}.com but some differ. Supporting common ones.
    domain_map = {"aexp": "aexp.com", "paypal": "paypal.com"}
    domain = domain_map.get(token, f"{token}.com")
    
    url = f"https://{token}.eightfold.ai/api/apply/v2/jobs"
    params = {"domain": domain, "query": query, "sort_by": "relevance"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        jobs = data.get("positions", [])
        return [
            {
                "id":         f"ef_{token}_{j['id']}",
                "title":      j.get("name", ""),
                "location":   j.get("location", ""),
                "url":        f"https://{token}.eightfold.ai/careers?jobId={j['id']}",
                "department": j.get("department", ""),
                "description": f"View on 八fold. {j.get('department','')}",
                "posted_date": datetime.datetime.now().strftime("%Y-%m-%d"),
            }
            for j in jobs
        ]
    except Exception as e:
        print(f"  [eightfold/{token}] error: {e}")
        raise e


def scrape_career_link_html(html: str, name: str, base_url: str) -> list[dict]:
    """Parse jobs from HTML career page."""
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    seen_titles = set()

    for el in soup.find_all(["a", "h2", "h3", "h4", "span", "li"]):
        text = el.get_text(strip=True)
        low_text = text.lower()
        if not any(kw in low_text for kw in PM_KEYWORDS):
            continue
        if len(text) > 120 or len(text) < 8:
            continue
        if any(x in text for x in ["{", "}", "<", ">", "=", "\\", "[", "]"]):
            continue
        noise = ["results", "found", "search", "all jobs", "sign in", "career",
                 "browse", "filter", "sort", "view all"]
        if any(x in low_text for x in noise) and low_text != "product manager":
            continue

        job_url = base_url
        if el.name == "a" and el.get("href"):
            job_url = urljoin(base_url, el["href"])
        else:
            p = el.find_parent("a")
            if p and p.get("href"):
                job_url = urljoin(base_url, p["href"])

        if job_url == base_url:
            continue

        location = "Unknown"
        parent = el.find_parent(["div", "li", "section", "tr"])
        if parent:
            loc_el = parent.find(class_=re.compile(r"location|city|state|place"))
            if loc_el:
                location = loc_el.get_text(strip=True)

        title = text.title()
        if title not in seen_titles:
            jobs.append({
                "id":          f"link_{hashlib.md5(title.encode()).hexdigest()[:8]}",
                "title":       title,
                "location":    location,
                "url":         job_url,
                "department":  "Various",
                "description": f"Found on {name} careers page.",
                "posted_date": datetime.date.today().isoformat(),
            })
            seen_titles.add(title)

    return jobs[:15]


def scrape_career_link(url: str, name: str) -> list[dict]:
    """Fallback: Scrape titles and direct links using BeautifulSoup."""
    # Enhanced headers to avoid 403
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/"
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        return scrape_career_link_html(r.text, name, url)
    except Exception as e:
        print(f"  [career_link/{name}] error: {e}")
        raise


def is_us_location(location: str) -> bool:
    if not location:
        return False
    loc = location.lower()
    international = ["india", "china", "london", " uk", "united kingdom",
                     "germany", "poland", "ireland", "mexico", "hong kong",
                     "singapore", "belfast", "manila", "mumbai", "bangalore",
                     "hyderabad", "canada", "toronto", "australia"]
    if any(x in loc for x in international):
        return False
    us_indicators = ["usa", "united states", "remote", "u.s.", " us ", "- us",
                     "nationwide", "anywhere in us"]
    if any(x in loc for x in us_indicators):
        return True
    states = ["al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il",
              "in","ia","ks","ky","la","me","md","ma","mi","mn","ms","mo","mt",
              "ne","nv","nh","nj","nm","ny","nc","nd","oh","ok","or","pa","ri",
              "sc","sd","tn","tx","ut","vt","va","wa","wv","wi","wy","dc"]
    for s in states:
        if f", {s}" in loc or f" {s} " in loc or loc.endswith(f" {s}") or f"- {s}" in loc:
            return True
    return False


def filter_pm_jobs(jobs: list[dict]) -> list[dict]:
    results = []
    for j in jobs:
        title_lower = j.get("title", "").lower()
        location    = j.get("location", "")
        is_pm = any(kw in title_lower for kw in PM_KEYWORDS)
        is_us = is_us_location(location)
        if is_pm and is_us:
            results.append(j)
    return results


def fetch_jobs_for_company(company: dict) -> list[dict]:
    ats   = company["ats"]
    token = company["token"]
    name  = company["name"]
    path  = company.get("path", "External")
    print(f"  Fetching {name} ({ats})...")

    if ats == "greenhouse":
        jobs = scrape_greenhouse(token)
    elif ats == "workday_search":
        jobs = scrape_workday_search(token, path)
    elif ats == "smartrecruiters":
        jobs = scrape_smartrecruiters(token)
    elif ats == "oracle_cloud":
        jobs = scrape_oracle_cloud(token)
    elif ats == "eightfold":
        jobs = scrape_eightfold(token)
    elif ats == "goldman":
        jobs = scrape_goldman()
    elif ats == "career_link":
        jobs = scrape_career_link(token, name)
    else:
        jobs = []

    pm_jobs = filter_pm_jobs(jobs)
    for j in pm_jobs:
        j["company"] = name
    print(f"    → {len(pm_jobs)} PM jobs (from {len(jobs)} total)")
    return pm_jobs


# ─── AI SCORING ──────────────────────────────────────────────────────────────

def score_job_with_ai(job: dict) -> dict:
    from google import genai

    if not GEMINI_API_KEY:
        job.update({"score": 0, "match_reason": "No API key",
                    "apply_now": False, "seniority": "unknown", "location_type": "unknown"})
        return job

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""You are a career advisor. Score this job against the candidate's profile.

RULE: Reject (score < 40) any role requiring 7+ years or Director/VP level unless the candidate
clearly qualifies. Senior PM roles requiring 3-5 years ARE a good match.

CANDIDATE PROFILE:
{RESUME_SUMMARY}

JOB:
Company: {job['company']}
Title: {job['title']}
Location: {job['location']}
Department: {job.get('department','')}
Description: {job.get('description','')}
URL: {job['url']}

Return ONLY valid JSON (no markdown fences):
{{
  "score": <0-100>,
  "match_reason": "<one sentence>",
  "apply_now": <true if score >= 70>,
  "seniority": "<entry/associate/mid/senior/director>",
  "location_type": "<remote/hybrid/onsite/unknown>"
}}"""

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            text = response.text.strip()
            for fence in ["```json", "```"]:
                text = text.replace(fence, "")
            result = json.loads(text.strip())
            job.update(result)
            return job
        except Exception as e:
            err = str(e)
            if "SAFETY" in err or "blocked" in err.lower() or "finish_reason" in err.lower():
                job.update({"score": 0, "match_reason": "Blocked by safety filter",
                            "apply_now": False, "seniority": "unknown", "location_type": "unknown"})
                return job
            if "429" in err or "Resource exhausted" in err or "RESOURCE_EXHAUSTED" in err:
                wait = (attempt + 1) * 30
                print(f"    [Rate limit] waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"    [AI score error] {e}")
                break

    job.update({"score": 0, "match_reason": "AI scoring failed",
                "apply_now": False, "seniority": "unknown", "location_type": "unknown"})
    return job


# ─── DEDUP ───────────────────────────────────────────────────────────────────

def load_seen_ids() -> set:
    if SEEN_IDS_FILE.exists():
        return set(json.loads(SEEN_IDS_FILE.read_text()))
    return set()

def save_seen_ids(seen: set):
    SEEN_IDS_FILE.write_text(json.dumps(list(seen)))


# ─── JSON DATABASE ────────────────────────────────────────────────────────────

def write_to_json(jobs: list[dict], scanned: int = 0, total: int = 0,
                  status: str = "running", matches: int = 0, failures: int = 0):
    try:
        data_file = Path("jobs.json")
        if data_file.exists():
            try:
                raw = json.loads(data_file.read_text())
                existing_jobs = raw.get("jobs", []) if isinstance(raw, dict) else raw
            except Exception:
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

        existing_jobs.sort(
            key=lambda x: (x.get("score", 0), x.get("fetch_date", "")),
            reverse=True
        )

        output = {
            "metadata": {
                "last_updated":       today,
                "scanned_count":      scanned,
                "total_companies":    total,
                "matches_found":      len(existing_jobs),
                "technical_failures": failures,
                "status":             status,
            },
            "jobs": existing_jobs,
        }

        data_file.write_text(json.dumps(output, indent=2))

        if added > 0:
            print(f"  ✓ Added {added} new jobs to jobs.json ({scanned}/{total})")
        else:
            print(f"    Progress: {scanned}/{total} scanned")

    except Exception as e:
        print(f"[JSON error] {e}")


# ─── EMAIL ALERT ─────────────────────────────────────────────────────────────

def send_email_alert(top_jobs: list[dict]):
    if os.getenv("ENABLE_EMAIL_ALERTS", "false").lower() != "true":
        return
    if not top_jobs or not SENDGRID_KEY or not ALERT_EMAIL:
        return
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail
        sg   = sendgrid.SendGridAPIClient(SENDGRID_KEY)
        body = "\n\n".join(
            f"{j['company']} — {j['title']}\nScore: {j['score']}/100\n"
            f"{j['match_reason']}\n{j['url']}"
            for j in top_jobs
        )
        msg = Mail(
            from_email="jobmonitor@yourdomain.com",
            to_emails=ALERT_EMAIL,
            subject=f"[Job Monitor] {len(top_jobs)} new PM jobs to apply for",
            plain_text_content=body,
        )
        sg.send(msg)
        print(f"  ✓ Email alert sent ({len(top_jobs)} jobs)")
    except Exception as e:
        print(f"[Email error] {e}")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"Job Monitor  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    seen_ids           = load_seen_ids()
    all_new            = []
    apply_now          = []
    total_companies    = len(COMPANIES)
    matches_found      = 0
    technical_failures = 0

    for i, company in enumerate(COMPANIES, 1):
        print(f"  [{i}/{total_companies}] {company['name']}")
        write_to_json([], scanned=i, total=total_companies,
                      matches=matches_found, failures=technical_failures)
        try:
            jobs     = fetch_jobs_for_company(company)
            new_jobs = [j for j in jobs if j["id"] not in seen_ids]
            has_match = False

            if new_jobs:
                print(f"    Scoring {len(new_jobs)} new jobs...")
                for job in new_jobs:
                    scored = score_job_with_ai(job)
                    seen_ids.add(job["id"])
                    if scored.get("score", 0) >= MATCH_THRESHOLD:
                        all_new.append(scored)
                        has_match = True
                    if scored.get("apply_now"):
                        apply_now.append(scored)
                    time.sleep(2.0)   # be gentle on Gemini rate limits

                write_to_json(all_new, scanned=i, total=total_companies,
                              matches=matches_found + (1 if has_match else 0),
                              failures=technical_failures)

            if has_match:
                matches_found += 1

            write_to_json(all_new, scanned=i, total=total_companies,
                          matches=matches_found, failures=technical_failures)
            save_seen_ids(seen_ids)

        except Exception as e:
            print(f"    ✗ Error: {e}")
            technical_failures += 1
            write_to_json(all_new, scanned=i, total=total_companies,
                          matches=matches_found, failures=technical_failures)

    all_new.sort(key=lambda x: x.get("score", 0), reverse=True)
    apply_now.sort(key=lambda x: x.get("score", 0), reverse=True)

    print(f"\n{'='*60}")
    print(f"Done! {len(all_new)} new matches (score ≥ {MATCH_THRESHOLD})")
    print(f"Apply now:  {len(apply_now)} jobs")
    print(f"Failures:   {technical_failures}/{total_companies}")
    print(f"{'='*60}\n")

    for j in all_new[:10]:
        print(f"  [{j['score']:3d}] {j['company']} — {j['title']}")
        print(f"        {j['match_reason']}")
        print(f"        {j['url']}\n")

    write_to_json([], scanned=total_companies, total=total_companies, status="complete")
    send_email_alert(apply_now)

    print("✓ Run fully complete")


if __name__ == "__main__":
    main()
