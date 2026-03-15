# Approach: RSS New-Item Notifications via Storage + Email

**Goal:** Track which RSS items have already been notified, and email **mark.a.seery@gmail.com** the title and link for any **new** items only.

**Note:** This codebase has a **storage server** (with namespaces as a dimension) and a **registry** (service discovery). There is no separate "namespace server" — we use the storage server's **namespace** feature (e.g. `rss_notified`) to partition state.

---

## 1. Storage server: what to store

- **Namespace:** e.g. `rss_notified` (dedicated namespace for “already notified” state).
- **Key:** One record **per feed URL** (URL-encoded, same pattern as `stored_webscrape_skill` with `webscrape` namespace).
- **Value (JSON):** A single object per feed that lists which items have been notified, so we can diff against new fetches.

**Option A – One record per feed, set of item IDs (recommended)**

- **Key:** `urlsafe_encode(feed_url)` (e.g. `https%3A%2F%2Fexample.com%2Ffeed.xml`).
- **Value:**
  ```json
  {
    "feed_url": "https://example.com/feed.xml",
    "updated_at": "2025-03-10T12:00:00Z",
    "notified_ids": ["guid-1", "guid-2", "https://example.com/post/1"]
  }
  ```
- **Item identity:** Use item `id` from rss_skill output; if missing, use `link`. Store that string in `notified_ids`.
- **Pros:** Bounded keys (one per feed), simple GET one record per feed, easy to update (GET → merge new ids → PUT).
- **Cons:** Record size grows with feed history; can cap to last N ids or hash old ones if needed later.

**Option B – One record per item**

- **Key:** e.g. `urlsafe_encode(feed_url) + "|" + urlsafe_encode(item_id_or_link)`.
- **Value:** `{ "notified_at": "ISO8601", "title": "...", "link": "..." }`.
- **Pros:** “Is this item notified?” = single GET; no large list in one record.
- **Cons:** Unbounded number of keys per namespace; listing “all notified for this feed” requires listing all keys and filtering (or a separate index). Heavier for many feeds.

**Recommendation:** Option A — one record per feed with a `notified_ids` list (and optional `updated_at`). Cap list length (e.g. last 500–1000) if feeds are very long-lived to avoid huge payloads.

---

## 2. Registry (and discovery)

- Use **registry** (e.g. `REGISTRY_SERVER_URL`) to resolve:
  - **Storage** base URL (for GET/PUT namespaces/records).
  - **Worker** base URL (for rss_skill and notification_skill).
- No separate “namespace server” is required; the storage server’s **namespace** is the partition (e.g. `rss_notified`).

---

## 3. Email: how to send

- Use the existing **notification_skill** (SMTP).
- **Endpoint:** `POST {worker_url}/skills/notification_skill/send`
- **Body:** `{ "to": ["mark.a.seery@gmail.com"], "subject": "...", "body": "..." }`
- **Content:** For each new item, send a short email:
  - **Subject:** e.g. `New RSS: <feed title> — <item title>` (truncate if needed).
  - **Body:** Plain text with item **title** and **link** (and optionally feed name/source).
- **Config:** Ensure notification_skill is configured (e.g. `EMAIL_SENDER`, `EMAIL_PASSWORD`, `SMTP_*` in `.env`). Recipient can be fixed in script/skill or via `EMAIL_RECEIVER_DEFAULT`.

---

## 4. End-to-end flow (per run)

1. **Resolve URLs** from registry: storage base URL, worker base URL.
2. **Ensure skills loaded** on worker: `rss_skill`, `notification_skill` (same pattern as `process_rss_feeds.py` / `website_marketing_analysis.py`).
3. **For each feed URL** in the configured list:
   - **Fetch feed:** `POST {worker}/skills/rss_skill/feed` with `{"url": feed_url}` → normalized `items[]` with `id`, `title`, `link`, etc.
   - **Load “already notified” state:**  
     `GET {storage}/namespaces/rss_notified/records/{encoded_feed_url}`  
     If 404 → treat as `notified_ids = []`.
   - **Compute new items:** For each item, take `item.id or item.link`; if not in `notified_ids`, consider it new.
   - **For each new item:**
     - Send email via `POST {worker}/skills/notification_skill/send` (to mark.a.seery@gmail.com, subject + body with title and link).
     - Append that item’s id (or link) to `notified_ids`.
   - **Persist updated state:**  
     `PUT {storage}/namespaces/rss_notified/records/{encoded_feed_url}` with body  
     `{ "feed_url": feed_url, "updated_at": "ISO8601", "notified_ids": updated_list }`.
4. **Idempotency / ordering:** First run for a feed notifies for all current items (everything is “new”). Subsequent runs only new items. Optionally cap `notified_ids` length (e.g. keep last 500) to avoid unbounded growth.

---

## 5. Where to implement

**Option 1 – Script (cron-friendly)**  
- New script (e.g. `rss_notify_new.py`) or a mode in `process_rss_feeds.py` (e.g. `--notify-new`).
- Script:
  - Reads feed list (default list or from file/args).
  - Uses registry → storage + worker.
  - For each feed: fetch via rss_skill, GET/PUT storage for `rss_notified`, send email for new items via notification_skill.
- **Schedule:** Run via cron (e.g. every 15–60 minutes).

**Option 2 – Skill**  
- New skill (e.g. `rss_new_skill`) that exposes something like:
  - `POST /check-and-notify` with body `{ "feed_urls": [...], "to_email": "mark.a.seery@gmail.com" }`.
  - Skill uses storage (same namespace/key scheme) and calls notification_skill (HTTP to same worker or another) to send email.
- A cron job or external scheduler then calls this skill’s endpoint periodically.

**Option 3 – Hybrid**  
- Script that invokes a “notify new” skill so logic lives in one place; script only does discovery + scheduling.

**Recommendation:** Start with **Option 1 (script)** for simplicity and easy cron scheduling; refactor into a skill later if you want an HTTP API for the same behavior.

---

## 6. Configuration summary

| What | Where |
|------|--------|
| Storage namespace | Fixed in code: `rss_notified` |
| Feed list | Script default list (e.g. same as `process_rss_feeds.py`) or CLI/config file |
| Recipient email | `mark.a.seery@gmail.com` (in script/skill or env e.g. `RSS_NOTIFY_EMAIL`) |
| Storage/worker URLs | From registry (`REGISTRY_SERVER_URL`) |
| Email sending | notification_skill (existing `.env`: `EMAIL_SENDER`, `EMAIL_PASSWORD`, `SMTP_*`) |

---

## 7. Edge cases and tweaks

- **Item identity:** Some feeds have no `id`; use `link` as fallback (rss_skill already normalizes this).
- **Duplicates:** Same item in multiple feeds → one notification per (feed, item). If you want one email per item globally, use a **global** namespace keyed by `item_id`/link only (e.g. namespace `rss_notified_global`, key = item id/link).
- **Throttling:** notification_skill has per-recipient throttling; batching many new items might hit it. Options: batch several items into one email (e.g. “3 new items: …”) or add small delays between sends.
- **Errors:** If email fails for one item, you can skip adding it to `notified_ids` so it will be retried next run; or add it anyway and log the failure (avoid duplicate emails).
- **First run:** All current items are “new” and will be emailed; consider a “dry run” or “since” cutoff (e.g. only notify for items published in the last 24 h) if the list is huge.

---

## 8. Summary

- **Storage server:** Namespace `rss_notified`, one record per feed URL (key = encoded feed URL), value = `feed_url`, `updated_at`, `notified_ids[]`.
- **Registry:** Used only to discover storage and worker URLs; no separate “namespace server.”
- **Email:** Existing notification_skill, POST `/skills/notification_skill/send`, to mark.a.seery@gmail.com with title + link for each new item.
- **Implementation:** Script (e.g. `rss_notify_new.py` or `--notify-new` in `process_rss_feeds.py`) that runs periodically (cron), fetches feeds via rss_skill, diffs against storage, sends email for new items, then updates storage.
