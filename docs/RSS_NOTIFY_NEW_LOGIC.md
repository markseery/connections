# rss_notify_new.py — logic validation

## Line-by-line review

### Config and discovery (lines 36–79)
- **REGISTRY_URL**: From env or default 7002; used for worker and storage discovery. Correct.
- **_worker_url()**: `find_live_worker(REGISTRY_URL)`; raises if none. Correct.
- **_storage_url()**: GET `{REGISTRY_URL}/servers/storage`, expect `{"url": "..."}`. Registry must return a single storage base URL. If it returns different URLs per run (e.g. load-balanced), tracking will break — same feed would hit different backends and always see 404.
- **_receiver_email()**: Requires `EMAIL_RECEIVER_DEFAULT` in env. Correct.

### Skills and fetch (lines 88–104)
- **_ensure_skills_loaded()**: POST to `/worker/skills/{name}/load` for rss_skill and notification_skill. Correct.
- **_fetch_feed()**: POST to `/skills/rss_skill/feed` with `{"url": feed_url}`; returns `None` on non-success, else JSON. Correct.

### Storage key (lines 106–110)
- **_storage_key()**: `strip().rstrip("/")` so `https://x/feed` and `https://x/feed/` map to the same key. Correct.

### GET notified state (lines 112–125)
- Builds key from `_storage_key(feed_url)`, then `quote(..., safe="")` for the path.
- GET `{storage_base}/namespaces/rss_notified/records/{key}`.
- 404 → return `[]`. Otherwise parse JSON; `value = data["value"]`; `notified_ids = value.get("notified_ids")`; return `list(ids)` if list else `[]`. Correct. Assumes storage returns `{"namespace", "key", "value"}` and `value` has `notified_ids` (list).

### PUT notified state (lines 127–144)
- Same key construction as GET. Body: `feed_url` (normalized), `updated_at`, `notified_ids` (capped at `MAX_NOTIFIED_IDS`). PUT same URL. Correct. Same key as GET so same record is updated.

### Notification and item id (lines 146–162)
- **_send_notification()**: POST to notification_skill/send; returns `r.is_success`. Correct.
- **_item_id()**: id or link or title, else `str(id(item))`. Correct for stable identity.

### Date filter (lines 164–198)
- **_parse_published()**: Tries ISO (with Z→+00:00), then `parsedate_to_datetime`, then YYYY-MM-DD prefix. Returns UTC datetime or None. Correct.
- **_published_within_days()**: None → True (include); else compare to `now - timedelta(days)`. Correct.

### process_feed (lines 200–237)
- Fetch feed; on failure return `([], [], False)`.
- Get items; if none, return `([], [], True)`.
- `notified_ids = _get_notified_state(storage_base, feed_url)` then `seen = set(notified_ids)`.
- `new_items = [i for i in items if _item_id(i) not in seen]`.
- For each new item: append `item_id` to `ids_to_add` (always). If `_published_within_days(published, MAX_AGE_DAYS)` then also append to `entries`. So old items are marked seen but not emailed. Correct.
- Returns `(entries, ids_to_add, True)`. So `ids_to_add` is the full set of new ids for this feed; `entries` is the subset that are within the age window. Correct.

### Email body and main (lines 239–354)
- **_format_published()**: Display only; YYYY-MM-DD if possible. Correct.
- **_build_summary_email()**: Subject and body from entries. Correct.
- **main**: Parse args; resolve to_email, worker_url, storage_base; load skills; set feeds (args or DEFAULT_FEEDS).
- Loop over feeds: call `process_feed(worker_url, storage_base, feed_url)`. Extend `all_entries` with `entries`; if `ids_to_add` non-empty append `(feed_url, ids_to_add)` to `storage_updates`. Correct.
- **apply_storage_updates()**: For each `(feed_url, ids_to_add)` get current `notified_ids`, extend with `ids_to_add`, put. So we merge this run’s new ids into existing state. Correct.
- **When to apply**:
  - If `storage_updates` is empty: no write. Correct.
  - If `all_entries` non-empty (something to email): dry-run → no send, no storage write. Else send email; if send ok → apply_storage_updates(). If send fails → do not update storage so next run retries. Correct.
  - If `storage_updates` non-empty but `all_entries` empty (all new items older than 30 days): dry-run → no storage write; else apply_storage_updates() so we still mark those items as seen. Correct.

### Summary
- Tracking correctness depends on: (1) same storage key for same feed (`_storage_key` + same feed list), (2) single persistent storage URL from registry, (3) merge on update (GET → extend → PUT). Logic in the script is consistent with that.
- If “385 every time” persists after the 30-day filter: either storage is not persistent / not the same URL every run, or the registry returns a different storage URL per run. The 30-day filter reduces emailed items and ensures we still call `apply_storage_updates()` when all new items are old, so we persist “seen” and reduce the number of items that would be re-counted as new on the next run.
