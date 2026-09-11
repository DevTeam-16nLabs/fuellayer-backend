# Diary persistence and history

Implemented after the user selected option 3, “Week in focus”, on 9 September 2026.

## Migration and rollout

Apply `alembic upgrade head` before releasing the mobile changes. Revision `20260909_13`, after existing `20260909_12`, adds `diary_entries`, `diary_revisions`, `diary_receipts`, `diary_days`, `users.diary_revision`, and `nutrition_target_history.verified` (default false). It is additive and does not alter pantry, purchases, plans or cooking state. It was applied successfully to the local PostgreSQL development database. No production deployment was performed. Downgrading drops diary data; do not use downgrade as a routine rollback once users have recorded entries.

The global `alembic check` reports existing index/unique-constraint representation differences, the catalogue search index, and a cooking check constraint. No differences were reported for the new diary tables. Those unrelated definitions were left unchanged.

## Authenticated API

All routes are under `/api/v1/me/diary`, use the existing Clerk `require_auth_subject`, resolve the completed account from that subject, and send `Cache-Control: private, no-store`. Account IDs cannot be supplied in mutation bodies. Each route uses account-scoped database queries.

| Route | Contract |
| --- | --- |
| `GET /range?start=YYYY-MM-DD&end=YYYY-MM-DD` | Inclusive range, at most 93 dates. Returns `owner`, `records` including tombstones, `covered_dates`, date-specific `targets`, `has_history`. Fetching a range does **not** advance the changes cursor. |
| `GET /changes?after=0&limit=200` | Monotonic account change sequence; latest revision of each changed entry including deletion. Returns `owner`, `records`, `cursor`, `has_more`; limit 1–200. |
| `POST /mutations` | Required `Idempotency-Key` (8–160 chars). Body below. Returns `{owner, record}`. |
| `GET /weekly?start=YYYY-MM-DD&today=YYYY-MM-DD` | Monday-start week, seven dates, actual recorded totals and missing macro counts, per-nutrient averages with sum/denominator/mean, historical target metadata. `today` is the client's local calendar date used to label unrecorded future dates. |

Example mutation:

```json
{
  "action": "put",
  "entry_id": "stable-client-entry-id",
  "diary_date": "2026-09-08",
  "expected_revision": 0,
  "timezone": "Africa/Dakar",
  "offset_minutes": 0,
  "entry": {
    "id": "stable-client-entry-id",
    "name": "Recorded food",
    "slot": "Lunch",
    "calories": 420,
    "macros": {"protein_g": 30, "carbohydrates_g": 48, "fat_g": 12},
    "servings": 1
  }
}
```

Actions: `put` creates/edits, `delete` retains a tombstone/snapshot, `restore` explicitly restores a revision, `migrate` imports an existing device entry. Creation expects revision 0; subsequent changes require the revision opened by the editor. `delete` omits `entry`; other writers retain the full entry snapshot. Each response record adds the literal date, original timezone/offset, revision, change sequence, deletion flag and UTC update timestamp.

Strict validation rejects unknown fields, malformed/nonexistent dates, non-IANA timezones, non-finite/negative nutrition, zero/negative portions, mismatched IDs and unsupported meal slots. Calories are bounded to 20,000, each macro to 5,000 g, consumed/reference quantities to 10,000. Food snapshots preserve source metadata, reference nutrition, and amount eaten. IDs are stable within each account; two accounts may independently use the same entry ID.

## Concurrency, retries, deletion

A database lock on the account row serializes revision and receipt checks across database connections/workers, including first-entry creation. Successful mutation receipts store the request hash and exact result. An identical retry returns that result; reusing a key with different content returns 409. Stale revisions/different recorded dates return 409 with `detail.current`. Validation failures return 422. The client never silently overwrites a conflicting revision. Independent entries continue syncing when one entry is blocked.

Every accepted revision stores a separate audit snapshot. Delete is a versioned tombstone; undo is an explicit version-checked restore of the saved consumed portion and nutrition. A later remote edit/restoration causes an undo conflict. Diary writes do not call pantry, courses, plan, recipe or cooking mutation services.

## Dates, nutrition, targets

`diary_date` is a calendar date, not a timestamp-derived query. Editing yesterday keeps yesterday even after midnight. A timezone change never moves an existing entry or its original day context. Existing records remain visible/editable if travel puts the device on an earlier local date; creating genuinely future entries is rejected. Legacy imports have unknown timezone and retain the old literal date; their date can be one that has already begun somewhere on Earth.

Amounts eaten are independent of recipe yield/planned personal portions. No historical read looks up current recipe/catalogue nutrition: it uses the entry's recorded totals and food snapshot. Macro-null entries use known snapshot nutrients; missing nutrients remain missing.

New onboarding and target-changing preference saves create verified prospective target events. Earlier baseline rows stay unverified. Existing users without verified history remain unavailable until a prospective target event establishes later history. A full-day target is shown only when a verified event predates that day's start in its stored timezone. Intraday changes are exposed explicitly, not averaged; a day with no established starting target stays unavailable. Unknown timezone means unavailable. Today's target is never substituted for a historical target.

For weekly averages, a day with active records participates in energy even when its total is genuinely zero. No-entry and unrecorded future days do not become zero-intake days. Macro eligibility is independent for protein/carbohydrate/fat: all recorded entries that day must have that macro available. Averages report each actual denominator; no eligible days means `mean: null`. Entries never imply a completely logged day. Records whose calendar date appears ahead after timezone travel remain recorded; only unrecorded dates are classified as future.

## Verification

- Full backend suite: **175 passed**, 9 skipped (7 PostgreSQL opt-ins run separately below; 2 pre-existing opt-ins).
- `FUELLAYER_DIARY_POSTGRES_TEST=1 .venv/bin/pytest tests/test_diary_postgres.py -q -s`: **7 passed**. Disposable PostgreSQL schemas, separate DB sessions, and a loopback HTTP server with test-only auth dependency overrides. Production auth is unchanged.
- The actual TypeScript `DiaryEngine` and production sharding/chunking logic use disk files against that HTTP server: restartable legacy migration, lost response retries, stale editor conflict, deletion/restart/undo, offline recovery, account isolation, snapshots, immutable date/timezone and client/server aggregation parity passed. This is not a visual fixture test.
- Backend diary Ruff checks pass. Local additive migration applied. Global schema drift described above remains outside this change.

Still pending: actual Clerk session lifecycle and two-device sync, native SecureStore crash/lock recovery and OS-level timezone transitions on connected devices. Unit/integration tests cover the logic; no connected MobAI device was available for device validation.
