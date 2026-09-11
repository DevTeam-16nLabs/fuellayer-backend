# Recipe imports: implementation and operations

Implemented after approval of Product Design option 1, Recipe sheet, on 8 September 2026. The mobile implementation and validation report are in `fuellayer-mobile/docs/design/recipes/import/IMPLEMENTATION.md`.

## Run locally or under a process supervisor

Apply migrations with `.venv/bin/alembic upgrade head`. Migration `20260908_11` adds seven tables without changing existing recipe catalogue records. It was applied to the local PostgreSQL database during validation. Its downgrade refuses to drop nonempty imported recipe data.

Start the API normally and run a separate worker:

```sh
.venv/bin/uvicorn fuellayer.main:app --host 0.0.0.0 --port 8000
.venv/bin/python -m fuellayer.modules.recipe_imports.worker
```

Production must supervise/restart the worker, provide outbound DNS/HTTP(S), and apply the migration before serving the mobile release. API startup deliberately does not launch receipt processing or a recipe worker. A queue can remain pending if no recipe worker is running.

Structured JSON-LD imports need no AI key. Plain text and unstructured pages use `OPENROUTER_API_KEY` or `OPENAI_API_KEY` and `RECIPE_IMPORT_MODEL` (default `gpt-4.1-mini`). The common Responses transport is independent of both receipt and recipe workflows. It uses strict structured output, a bounded output budget, a total request timeout, and `store: false`. Text fallback without a configured provider reports failure; direct manual recipe editing still works.

## API

All paths below are under `/api/v1`, authenticated with the existing Clerk bearer-token dependency. Imported recipe IDs have the form `import:<uuid>`. Catalogue public endpoints remain available. Every mutation requires a UUID `Idempotency-Key`; changed payloads with the same key conflict. Versioned edits use `expected_version` and return 409 for stale data. Read results are private and not cached.

| Method and path | Body / outcome |
| --- | --- |
| POST `/recipe-imports` | `{input:{kind:"url",url}}`, `{input:{kind:"text",text,source_url?}}` or `{input:{kind:"manual"}}`; returns a durable job, 202 |
| POST `/recipes/imported` | Starts a blank manual draft, 201 |
| GET `/recipe-imports?offset=0` | Account-owned recent jobs and `next_offset` |
| GET `/recipe-imports/{job}` | Stage, version, result recipe ID, candidates, bounded source text, typed error |
| POST `/recipe-imports/{job}/selection` | `{expected_version,candidate_id}` |
| POST `/recipe-imports/{job}/retry` | `{expected_version}` for eligible transient failures |
| POST `/recipe-imports/{job}/fallback` | `{expected_version,input:{kind:"text",text}}` or manual input |
| DELETE `/recipe-imports/{job}` | Cancels processing and removes its unsaved working recipe |
| GET `/recipe-import-commands/{key}` | Resolve an uncertain mutation before resending the same command |
| GET `/recipes/{recipe}` | Owned import or authenticated catalogue detail |
| PUT `/recipes/imported/{recipe}` | Full editable `RecipeData` plus `expected_version`; preserves row identity/order and original evidence |
| POST `/recipes/imported/{recipe}/save` | `{expected_version,status:"draft"|"reviewed"}` |
| DELETE `/recipes/imported/{recipe}` | Idempotent deletion; preserves explicit plan/diary snapshots |
| GET `/recipes` | Existing library plus saved imported drafts/reviewed recipes |

`RecipeData`, exact validation bounds, nutrition sets and error response models are documented by the generated OpenAPI schema and `recipe_imports/schemas.py`. A reviewed save requires a title, servings, named ingredients with explicit amounts/units (or explicit as-needed wording), and nonempty preparation steps. Preparation time, author and nutrition can remain missing.

## Durability and isolation

Recipes, ordered rows, nutrition, source keys, commands and jobs are owned by an account. Child recipe rows use composite ownership foreign keys. API/service queries scope imported IDs by user, and mutations serialize on the existing account lock. Separate command records prevent retry duplication; normalized source aliases deduplicate repeated imports by account, including after retention cleanup. Private-account imports never enter the public catalogue.

Workers lease jobs for three minutes and checkpoint retrieved text and selected candidates. They check the lease before writing results. Expired leases resume after a restart, with at most three processing attempts. Transient failures use bounded backoff; cancellation/fallback invalidates the active lease. Network operations run outside the account transaction. Daily starts are capped at 30/account and two processing jobs/account.

Temporary text/checkpoints and unsaved working recipes expire after seven days. Explicitly saved drafts and reviewed recipes persist; their aliases continue preventing duplicates. Command reply payloads become tombstones after 30 days. Recipe deletion clears replay snapshots that contain the recipe. The user-owned recipe retains bounded source evidence needed for review and attribution.

## Retrieval boundary and data policy

Only public HTTP(S) on the standard ports is fetched. Every DNS answer and every redirect destination is checked. Connections pin a verified public address while maintaining TLS certificate/hostname validation and checking the socket peer. Credentials, private/local/metadata destinations and legacy numeric IP forms are rejected. There are at most three redirects, a 20-second overall fetch deadline and a 2 MiB limit on both transferred and decompressed content. The fetcher sends no account cookies, auth headers or proxy credentials and supports only HTML/plain text.

JSON-LD `Recipe` is preferred; several candidates require selection. The fallback AI selects exact source excerpts, which are checked against retrieved text; unsupported generated quantities or steps are discarded. Range/package ambiguity, missing nutrition basis and yield units stay explicit. No login, paywall, CAPTCHA, social-platform credential, browser execution, video transcription or private-link bypass is implemented.

Original reported nutrition is separate from manually entered values and Ciqual-based estimates. Estimates require deliberate food mapping and an explicit edible weight; volume/count density is never guessed. Ingredient/serving changes invalidate old source nutrition and compatibility confirmation. Catalogue hashes remain compatible with existing portion-adjusted plans. Imported replacement requires reviewed, scalable ingredients, complete nutrition with a known basis, and reviewed compatibility with the user's preferences. The plan stores an immutable owned snapshot, so subsequent recipe edits/deletion do not silently change a planned meal. Diary logging is still an explicit mobile action and retains nutrition/source snapshots.

Import/correction/save/delete do not mutate the plan, grocery list, receipt ledger, pantry or diary.

## Verification

Run `.venv/bin/pytest`, `.venv/bin/mypy` and `.venv/bin/ruff check src tests/test_recipe_imports.py tests/test_recipe_fetch.py`. Run `.venv/bin/python scripts/check_recipe_import_flow.py` for public-source + authenticated API + PostgreSQL smoke validation. It uses test-only dependency overrides inside a separate app instance, synthetic accounts and an outer rollback, never a production authentication bypass. It exercises real source retrieval and the configured AI provider. Results from 8 September 2026 are in the mobile implementation report. Publisher accessibility is not a permanent guarantee.
