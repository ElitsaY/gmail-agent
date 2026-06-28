"""
Gmail Real-time Filter
Runs every 5 minutes. Checks emails from the last 10 minutes and:
- Applies hardcoded + learned filter rules
- Labels + archives unimportant emails before notifications fire
- Detects urgent emails and sends an immediate alert
- Updates memory with sender stats
"""

import os
import json
import base64
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import anthropic
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

import memory as mem
import costs

# ── Config ────────────────────────────────────────────────────────────────────

YOUR_EMAIL = "eyotkova@gmail.com"
LABEL_NAME = "not important"
LOOKBACK_MINUTES = 10  # overlap to avoid missing emails between runs

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.send",
]

# Hardcoded spam — never calls Claude
KNOWN_SPAM = ["ryanair", "linkedin", "getyourguide", "isic"]

# ── Gmail auth ────────────────────────────────────────────────────────────────

def get_gmail_service():
    creds = None
    token_path = os.path.join(os.path.dirname(__file__), "token.json")
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, "w") as f:
                f.write(creds.to_json())
        else:
            raise RuntimeError("No valid credentials. Run setup_gmail.py first.")
    return build("gmail", "v1", credentials=creds)

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_header(headers: list, name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def get_or_create_label(service, name: str) -> str:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"].lower() == name.lower():
            return label["id"]
    new_label = service.users().labels().create(
        userId="me",
        body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
    ).execute()
    return new_label["id"]


def archive_and_label(service, email_id: str, label_id: str):
    service.users().messages().modify(
        userId="me",
        id=email_id,
        body={"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]},
    ).execute()


def send_urgent_alert(service, email: dict):
    subject = f"🚨 Urgent email: {email['subject']}"
    html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #ff4444; color: white; padding: 12px 20px; border-radius: 8px 8px 0 0;">
            <strong>🚨 Urgent email detected</strong>
        </div>
        <div style="border: 2px solid #ff4444; border-top: none; padding: 20px; border-radius: 0 0 8px 8px;">
            <p><strong>From:</strong> {email['sender']}</p>
            <p><strong>Subject:</strong> {email['subject']}</p>
            <p><strong>Preview:</strong> {email['snippet']}</p>
        </div>
    </div>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = YOUR_EMAIL
    msg["To"] = YOUR_EMAIL
    msg.attach(MIMEText(html, "html"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"  🚨 Urgent alert sent for: {email['subject'][:60]}")

# ── Email fetching ────────────────────────────────────────────────────────────

def fetch_recent_inbox_emails(service, minutes: int) -> list[dict]:
    since_ts = int((datetime.now(timezone.utc) - timedelta(minutes=minutes)).timestamp())
    query = f"after:{since_ts} in:inbox -from:{YOUR_EMAIL}"
    result = service.users().messages().list(userId="me", q=query, maxResults=50).execute()

    emails = []
    for msg_ref in result.get("messages", []):
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="metadata",
            metadataHeaders=["From", "Subject"]
        ).execute()
        headers = msg["payload"].get("headers", [])
        emails.append({
            "id": msg["id"],
            "sender": get_header(headers, "From"),
            "subject": get_header(headers, "Subject"),
            "snippet": msg.get("snippet", ""),
        })
    return emails

# ── Claude classification ─────────────────────────────────────────────────────

def classify_with_claude(emails: list[dict], frequent_contacts: list[str]) -> dict[str, dict]:
    """
    Classify emails and detect urgency.
    Returns dict of email_id -> {classification, urgent}
    """
    client = anthropic.Anthropic()

    contacts_note = ""
    if frequent_contacts:
        contacts_note = f"\nFrequent contacts (always mark important): {', '.join(frequent_contacts)}"

    email_list = "\n\n".join(
        f"ID: {e['id']}\nFrom: {e['sender']}\nSubject: {e['subject']}\nSnippet: {e['snippet']}"
        for e in emails
    )

    model = "claude-haiku-4-5"
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=(
            "You are filtering a personal inbox. For each email return classification and urgency.\n\n"
            "NOT IMPORTANT: newsletters, promotions, marketing, automated notifications, "
            "order confirmations, social media digests, surveys, ads, mass mailings.\n\n"
            "IMPORTANT: personal messages, work emails, bank alerts, travel confirmations, "
            "appointment reminders, anything requiring action or reply.\n\n"
            "URGENT (important AND time-sensitive): flight changes, cancellations, bank fraud alerts, "
            "payment failures, messages from known contacts needing immediate attention.\n\n"
            + contacts_note + "\n\n"
            "Return ONLY a JSON object mapping email ID to {\"classification\": \"important\"|\"not important\", \"urgent\": true|false}.\n"
            "Example: {\"id1\": {\"classification\": \"not important\", \"urgent\": false}}"
        ),
        messages=[{"role": "user", "content": email_list}],
    )

    costs.record(
        agent="filter",
        model=model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0),
        emails_processed=len(emails),
    )

    text = response.content[0].text.strip()
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end == 0:
        return {}
    return json.loads(text[start:end])

# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    now = datetime.now()

    # Sleep hours: midnight to 7:30am
    if now.hour < 7 or (now.hour == 7 and now.minute < 30):
        print(f"[{now.strftime('%H:%M:%S')}] Sleep hours (00:00–07:30). Skipping.")
        return

    timestamp = now.strftime("%H:%M:%S")
    memory = mem.load()
    service = get_gmail_service()
    label_id = get_or_create_label(service, LABEL_NAME)

    emails = fetch_recent_inbox_emails(service, minutes=LOOKBACK_MINUTES)

    if not emails:
        print(f"[{timestamp}] No new inbox emails.")
        return

    print(f"[{timestamp}] Checking {len(emails)} new email(s)...")

    # Split into filter categories
    hardcoded_spam, learned_spam, needs_claude = [], [], []
    for e in emails:
        if any(s in e["sender"].lower() for s in KNOWN_SPAM):
            hardcoded_spam.append(e)
        elif mem.is_learned_filter(memory, e["sender"]):
            learned_spam.append(e)
        else:
            needs_claude.append(e)

    # Claude classifies remaining emails
    claude_results = {}
    if needs_claude:
        claude_results = classify_with_claude(needs_claude, memory["frequent_contacts"])

    # Process each email
    archived = 0

    for e in hardcoded_spam:
        archive_and_label(service, e["id"], label_id)
        mem.record_filtered(memory, e["sender"])
        print(f"  ✕ [hardcoded]  {e['sender'][:45]} — {e['subject'][:45]}")
        archived += 1

    for e in learned_spam:
        archive_and_label(service, e["id"], label_id)
        mem.record_filtered(memory, e["sender"])
        print(f"  ✕ [learned]    {e['sender'][:45]} — {e['subject'][:45]}")
        archived += 1

    for e in needs_claude:
        result = claude_results.get(e["id"], {"classification": "important", "urgent": False})
        if result["classification"] == "not important":
            archive_and_label(service, e["id"], label_id)
            mem.record_filtered(memory, e["sender"])
            print(f"  ✕ [claude]     {e['sender'][:45]} — {e['subject'][:45]}")
            archived += 1
        else:
            mem.record_kept(memory, e["sender"])
            if result.get("urgent"):
                send_urgent_alert(service, e)
            print(f"  ✓ {'[URGENT]' if result.get('urgent') else '[kept]  '} {e['sender'][:45]} — {e['subject'][:45]}")

    print(f"  → {archived} archived, {len(emails) - archived} kept.")
    mem.save(memory)


if __name__ == "__main__":
    run()
