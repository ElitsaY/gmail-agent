"""
Gmail Orchestration Agent
Claude drives the inbox workflow via tools.
- Reads/writes memory to learn preferences over time
- Checks for user replies to previous digest suggestions
- Sends digest with adaptive filter suggestions
"""

import os
import base64
import json
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import anthropic
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

import memory as mem
import costs
import dashboard

# ── Config ────────────────────────────────────────────────────────────────────

YOUR_EMAIL = "eyotkova@gmail.com"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.send",
]

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

# ── Gmail helpers ─────────────────────────────────────────────────────────────

def get_header(headers: list, name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def get_body(payload: dict) -> str:
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        return get_body(payload["parts"][0])
    data = payload.get("body", {}).get("data", "")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    return ""

# ── Tool implementations ──────────────────────────────────────────────────────

def tool_search_emails(service, query: str, max_results: int = 50) -> list[dict]:
    result = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    emails = []
    for msg_ref in result.get("messages", []):
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]
        ).execute()
        headers = msg["payload"].get("headers", [])
        emails.append({
            "id": msg["id"],
            "thread_id": msg["threadId"],
            "sender": get_header(headers, "From"),
            "subject": get_header(headers, "Subject"),
            "date": get_header(headers, "Date"),
            "snippet": msg.get("snippet", ""),
            "label_ids": msg.get("labelIds", []),
        })
    return emails


def tool_get_email(service, email_id: str) -> dict:
    msg = service.users().messages().get(userId="me", id=email_id, format="full").execute()
    headers = msg["payload"].get("headers", [])
    return {
        "id": msg["id"],
        "thread_id": msg["threadId"],
        "sender": get_header(headers, "From"),
        "subject": get_header(headers, "Subject"),
        "date": get_header(headers, "Date"),
        "body": get_body(msg["payload"])[:3000],
        "snippet": msg.get("snippet", ""),
        "label_ids": msg.get("labelIds", []),
    }


def tool_apply_label(service, email_id: str, label_name: str) -> dict:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    label_id = next((l["id"] for l in labels if l["name"].lower() == label_name.lower()), None)
    if not label_id:
        new_label = service.users().labels().create(
            userId="me",
            body={"name": label_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
        ).execute()
        label_id = new_label["id"]
    service.users().messages().modify(
        userId="me", id=email_id,
        body={"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]},
    ).execute()
    return {"success": True, "label_id": label_id}


def tool_send_digest(service, subject: str, html_body: str) -> dict:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = YOUR_EMAIL
    msg["To"] = YOUR_EMAIL
    msg.attach(MIMEText(html_body, "html"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"success": True, "message_id": sent["id"], "thread_id": sent.get("threadId")}


def send_cost_report(service):
    """Send a daily cost summary email with a link to the live dashboard."""
    data = costs.load()
    runs = data.get("runs", [])
    today = datetime.now().date().isoformat()
    this_month = datetime.now().strftime("%Y-%m")

    today_cost = sum(r["cost_usd"] for r in runs if r["timestamp"].startswith(today))
    month_cost = sum(r["cost_usd"] for r in runs if r["timestamp"].startswith(this_month))
    total_cost = data.get("total_cost_usd", 0.0)
    today_runs = [r for r in runs if r["timestamp"].startswith(today)]
    filter_today = sum(r["cost_usd"] for r in today_runs if r["agent"] == "filter")
    digest_today = sum(r["cost_usd"] for r in today_runs if r["agent"] == "digest")

    dashboard_url = "https://elitsay.github.io/gmail-agent/"

    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 520px; margin: 0 auto; color: #1d1d1f;">
      <div style="background: #1d1d1f; color: white; padding: 20px 28px; border-radius: 12px 12px 0 0;">
        <h2 style="margin:0; font-size:18px;">💰 Gmail Agent — Daily Cost Report</h2>
        <p style="margin:6px 0 0; color:#aaa; font-size:13px;">{datetime.now().strftime("%A, %B %d, %Y")}</p>
      </div>
      <div style="border: 1px solid #e5e5e5; border-top: none; padding: 24px 28px; border-radius: 0 0 12px 12px;">
        <table style="width:100%; border-collapse:collapse; font-size:15px;">
          <tr>
            <td style="padding:10px 0; color:#888; border-bottom:1px solid #f0f0f0;">Today</td>
            <td style="padding:10px 0; text-align:right; font-weight:600; border-bottom:1px solid #f0f0f0;">${today_cost:.5f}</td>
          </tr>
          <tr>
            <td style="padding:10px 0; color:#888; border-bottom:1px solid #f0f0f0; padding-left:16px; font-size:13px;">↳ Filter agent</td>
            <td style="padding:10px 0; text-align:right; font-size:13px; color:#555; border-bottom:1px solid #f0f0f0;">${filter_today:.5f}</td>
          </tr>
          <tr>
            <td style="padding:10px 0; color:#888; border-bottom:1px solid #f0f0f0; padding-left:16px; font-size:13px;">↳ Digest agent</td>
            <td style="padding:10px 0; text-align:right; font-size:13px; color:#555; border-bottom:1px solid #f0f0f0;">${digest_today:.5f}</td>
          </tr>
          <tr>
            <td style="padding:10px 0; color:#888; border-bottom:1px solid #f0f0f0;">This month</td>
            <td style="padding:10px 0; text-align:right; font-weight:600; border-bottom:1px solid #f0f0f0;">${month_cost:.4f}</td>
          </tr>
          <tr>
            <td style="padding:10px 0; color:#888;">All time</td>
            <td style="padding:10px 0; text-align:right; font-weight:600;">${total_cost:.4f}</td>
          </tr>
        </table>
        <div style="margin-top:24px; text-align:center;">
          <a href="{dashboard_url}" style="background:#0071e3; color:white; padding:12px 28px; border-radius:8px; text-decoration:none; font-size:14px; font-weight:500;">
            View Full Dashboard →
          </a>
        </div>
      </div>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Gmail Agent cost — {today} (${today_cost:.5f} today)"
    msg["From"] = YOUR_EMAIL
    msg["To"] = YOUR_EMAIL
    msg.attach(MIMEText(html, "html"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"✓ Cost report sent (today: ${today_cost:.5f})")


def tool_read_memory() -> dict:
    return mem.load()


def tool_write_memory(updates: dict) -> dict:
    memory = mem.load()
    # merge updates into memory
    for key, value in updates.items():
        if key == "learned_filters" and isinstance(value, list):
            # deduplicate
            existing = set(memory.get("learned_filters", []))
            existing.update(value)
            memory["learned_filters"] = list(existing)
        elif key == "pending_suggestions" and isinstance(value, list):
            memory["pending_suggestions"] = value
        else:
            memory[key] = value
    mem.save(memory)
    return {"success": True, "memory": memory}


def tool_get_thread(service, thread_id: str) -> dict:
    """Get all messages in a thread — used to check for user replies to suggestions."""
    thread = service.users().threads().get(userId="me", id=thread_id, format="metadata").execute()
    messages = []
    for msg in thread.get("messages", []):
        headers = msg["payload"].get("headers", [])
        messages.append({
            "id": msg["id"],
            "sender": get_header(headers, "From"),
            "subject": get_header(headers, "Subject"),
            "date": get_header(headers, "Date"),
            "snippet": msg.get("snippet", ""),
        })
    return {"thread_id": thread_id, "messages": messages}

# ── Tool dispatcher ───────────────────────────────────────────────────────────

def execute_tool(service, tool_name: str, tool_input: dict) -> str:
    try:
        if tool_name == "search_emails":
            return json.dumps(tool_search_emails(service, tool_input["query"], tool_input.get("max_results", 50)))
        elif tool_name == "get_email":
            return json.dumps(tool_get_email(service, tool_input["email_id"]))
        elif tool_name == "apply_label":
            return json.dumps(tool_apply_label(service, tool_input["email_id"], tool_input["label_name"]))
        elif tool_name == "send_digest":
            return json.dumps(tool_send_digest(service, tool_input["subject"], tool_input["html_body"]))
        elif tool_name == "read_memory":
            return json.dumps(tool_read_memory())
        elif tool_name == "write_memory":
            return json.dumps(tool_write_memory(tool_input["updates"]))
        elif tool_name == "get_thread":
            return json.dumps(tool_get_thread(service, tool_input["thread_id"]))
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
    except Exception as e:
        return json.dumps({"error": str(e)})

# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "search_emails",
        "description": "Search Gmail. Returns emails with id, thread_id, sender, subject, date, snippet, label_ids.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query"},
                "max_results": {"type": "integer", "default": 50},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_email",
        "description": "Get full content of an email by ID.",
        "input_schema": {
            "type": "object",
            "properties": {"email_id": {"type": "string"}},
            "required": ["email_id"],
        },
    },
    {
        "name": "apply_label",
        "description": "Apply 'not important' label to an email and remove it from inbox.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string"},
                "label_name": {"type": "string", "description": "e.g. 'not important'"},
            },
            "required": ["email_id", "label_name"],
        },
    },
    {
        "name": "send_digest",
        "description": "Send the daily digest email to the user. Call once at the end.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "html_body": {"type": "string"},
            },
            "required": ["subject", "html_body"],
        },
    },
    {
        "name": "read_memory",
        "description": (
            "Read persistent memory. Contains: learned_filters (senders to always filter), "
            "sender_stats (how often each sender was filtered/kept), "
            "frequent_contacts (important people), "
            "pending_suggestions (filter suggestions waiting for user reply)."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "write_memory",
        "description": (
            "Update persistent memory. Use to add learned_filters when user confirms a suggestion, "
            "or to update pending_suggestions after sending the digest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "object",
                    "description": (
                        "Fields to update. Supported keys: "
                        "'learned_filters' (list of sender strings to add), "
                        "'pending_suggestions' (replace the full list)."
                    ),
                }
            },
            "required": ["updates"],
        },
    },
    {
        "name": "get_thread",
        "description": "Get all messages in a thread. Use to check if the user replied to a previous digest suggestion.",
        "input_schema": {
            "type": "object",
            "properties": {"thread_id": {"type": "string"}},
            "required": ["thread_id"],
        },
    },
]

# ── System prompt ─────────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    since_ts = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp())
    today = datetime.now().strftime("%A, %B %d, %Y")
    time_now = datetime.now().strftime("%H:%M")

    return f"""You are a personal email assistant. Today is {today}, {time_now}.

Your job is to process the Gmail inbox for {YOUR_EMAIL} and send a daily digest.

## Steps

1. **Read memory** — call read_memory to load learned filter rules, sender stats, frequent contacts, and any pending suggestions waiting for user replies.

2. **Check for user replies to suggestions** — for each item in pending_suggestions, call get_thread with its thread_id to see if the user replied. If they replied "yes" or confirmed filtering a sender, call write_memory to add that sender to learned_filters and clear the suggestion. If they said "no", just clear the suggestion.

3. **Fetch and classify emails** — search for emails with: after:{since_ts} -from:{YOUR_EMAIL}
   - Apply label "not important" to unimportant emails (this also removes them from inbox)
   - Update memory stats: track which senders were filtered vs kept
   - Frequent contacts (from memory) should always be marked important

4. **Identify filter candidates** — look at sender_stats in memory. Any sender filtered 3+ times with 0 kept emails is a candidate to add to learned_filters.

5. **Send the digest** — use send_digest with an HTML email containing:

   **📌 Important emails** — one bullet per email: sender, subject, 1-sentence summary, any action needed

   **🗂 Filtered today** — count + brief list of what was archived

   **💡 Suggestions** (only if there are filter candidates) — for each candidate:
   "I've filtered X emails from [sender] — reply YES to this email to always filter them automatically."
   Keep suggestions to max 3 per digest.

6. **Save pending suggestions** — after sending the digest, call write_memory to store the thread_id of the sent digest alongside each suggestion, so next run can check for your reply.

## Classification rules

NOT IMPORTANT: newsletters, promotions, marketing, automated notifications, order confirmations, social media digests, surveys, ads.

IMPORTANT: personal messages, work emails, bank alerts, travel confirmations, appointment reminders, anything requiring action or reply. Always keep emails from frequent_contacts.

Be efficient: classify from snippets when possible, only call get_email when the snippet isn't enough."""


# ── Agent loop ────────────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*50}")
    print(f"Gmail Agent — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")

    service = get_gmail_service()
    client = anthropic.Anthropic()

    messages = [
        {"role": "user", "content": "Process my inbox and send the daily digest."}
    ]

    model = "claude-opus-4-7"
    total_input, total_output, total_cache = 0, 0, 0

    while True:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=build_system_prompt(),
            tools=TOOLS,
            messages=messages,
        )

        total_input += response.usage.input_tokens
        total_output += response.usage.output_tokens
        total_cache += getattr(response.usage, "cache_read_input_tokens", 0)

        for block in response.content:
            if block.type == "text" and block.text:
                print(f"[Claude] {block.text}")
            elif block.type == "tool_use":
                print(f"[Tool]   {block.name}({json.dumps(block.input)[:100]})")

        if response.stop_reason == "end_turn":
            run_cost = costs.record(
                agent="digest",
                model=model,
                input_tokens=total_input,
                output_tokens=total_output,
                cache_read_tokens=total_cache,
            )
            print(f"\n✓ Done. Cost this run: ${run_cost:.4f}")
            dashboard.build_dashboard()
            dashboard.push_to_github()
            send_cost_report(service)
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(service, block.name, block.input)
                    print(f"         → {result[:150]}{'...' if len(result) > 150 else ''}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
        else:
            print(f"Unexpected stop: {response.stop_reason}")
            break


if __name__ == "__main__":
    run()
