# RepoGuide evaluation — 2026-10-07T23:09:08+00:00

- Evaluation set: **full-stack-fastapi-template** (21 questions, 3 unanswerable)
- Project: fastapi/full-stack-fastapi-template (`D:\CodeParser\data\workspace\github\fastapi__full-stack-fastapi-template`)
- Models: chat `qwen2.5-coder:7b`, embeddings `nomic-embed-text`
- Answer generation: on
- Duration: 64.4 s

## Summary

| Metric | Value |
|---|---|
| Hit rate @5 (expected symbol) | 0.857 |
| MRR @10 (expected symbol) | 0.632 |
| Hit rate @5 (expected file) | 0.857 |
| MRR @10 (expected file) | 0.708 |
| Relevant chunk in answer context (top-8 + graph) | 0.857 |
| Citation validity (40 citations checked) | 0.975 |
| Answers with at least one citation | 1.000 |
| Answers citing the expected file | 0.857 |
| Answers citing the expected symbol | 0.809 |
| Drafts rewritten because they cited nothing | 0.000 |
| Answers with a citation number corrected | 0.095 |
| Average answer time (s) | 2.778 |
| Abstention on unanswerable questions | 1.000 |

## Retrieval ablation

| Retriever | Hit@5 | MRR@10 |
|---|---|---|
| vector | 0.762 | 0.438 |
| bm25 | 0.619 | 0.477 |
| hybrid | 0.857 | 0.632 |

## Per-question results

| ID | Question | Expected | Rank | Hit@5 | In context | Citations valid | Cites expected file |
|---|---|---|---|---|---|---|---|
| r01 | How are user passwords hashed, and which hashing algorithms are supported? | `backend/app/core/security.py` · `get_password_hash` | 4 | yes | yes | 2/2 | yes |
| r02 | How is a JWT access token created and when does it expire? | `backend/app/core/security.py` · `create_access_token` | 1 | yes | yes | 2/2 | yes |
| r03 | How does the backend decode the bearer token and load the current user for a request? | `backend/app/api/deps.py` · `get_current_user` | 1 | yes | yes | 1/1 | yes |
| r04 | What stops a normal user from calling admin-only endpoints? | `backend/app/api/deps.py` · `get_current_active_superuser` | n/a | no | no | 1/1 | no |
| r05 | How does login avoid leaking, through response time, whether an email address exists? | `backend/app/crud.py` · `authenticate` | 1 | yes | yes | 1/1 | yes |
| r06 | How is a new user saved to the database with a hashed password? | `backend/app/crud.py` · `create_user` | 4 | yes | yes | 3/3 | yes |
| r07 | Which endpoint issues an OAuth2 access token after checking the email and password? | `backend/app/api/routes/login.py` · `login_access_token` | 1 | yes | yes | 2/2 | yes |
| r08 | How does password recovery avoid revealing whether an email is registered? | `backend/app/api/routes/login.py` · `recover_password` | 1 | yes | yes | 2/2 | yes |
| r09 | How is a password reset token checked before the new password is saved? | `backend/app/api/routes/login.py` · `reset_password` | 3 | yes | yes | 3/3 | yes |
| r10 | How is the password reset token generated and how long is it valid? | `backend/app/utils.py` · `generate_password_reset_token` | 1 | yes | yes | 1/1 | yes |
| r11 | How are emails sent and which SMTP options are used? | `backend/app/utils.py` · `send_email` | 1 | yes | yes | 2/2 | yes |
| r12 | How can someone sign up without being logged in, and what happens if the email is already taken? | `backend/app/api/routes/users.py` · `register_user` | 2 | yes | yes | 3/3 | yes |
| r13 | Can a superuser delete their own account? | `backend/app/api/routes/users.py` · `delete_user_me` | 4 | yes | yes | 2/2 | yes |
| r14 | What checks are made when users change their own password? | `backend/app/api/routes/users.py` · `update_password_me` | 9 | no | no | 2/2 | no |
| r15 | Which items does a user who is not an admin see when listing items? | `backend/app/api/routes/items.py` · `read_items` | 4 | yes | yes | 1/1 | yes |
| r16 | How is the first superuser account created when the database is initialised? | `backend/app/core/db.py` · `init_db` | 1 | yes | yes | 3/3 | yes |
| r17 | What happens if a secret is left set to 'changethis' in the configuration? | `backend/app/core/config.py` · `_check_default_secret` | 1 | yes | yes | 2/2 | yes |
| r18 | How long are access tokens valid by default, and where is that configured? | `backend/app/core/config.py` · `Settings` | n/a | no | no | 1/1 | no |
| r19 | Where does the frontend store the access token after login, and what does logout do? | `frontend/src/hooks/useAuth.ts` · `useAuth` | 3 | yes | yes | 2/2 | yes |
| r20 | How does the frontend decide whether the user is logged in? | `frontend/src/hooks/useAuth.ts` · `isLoggedIn` | 1 | yes | yes | 2/2 | yes |
| r21 | Where is Sentry error tracking set up, and when is it turned on? | `backend/app/main.py` | 1 | yes | yes | 1/2 | yes |

## Unanswerable questions (the model should say it cannot find the answer)

| ID | Question | Abstained | Answer (start) |
|---|---|---|---|
| u01 | How does the app charge customers through Stripe? | yes | I could not find this in the indexed code. The provided excerpts do not contain any information about how the app charges customers through… |
| u02 | Which Kafka topics does the backend publish events to? | yes | I could not find this in the indexed code. The provided excerpts do not mention any Kafka topics or event publishing. |
| u03 | How are WebSocket connections authenticated for real-time chat? | yes | I could not find this in the indexed code. The provided excerpts do not contain any information about WebSocket connections or real-time ch… |

## Misses (expected symbol not in top 5)

- **r04** What stops a normal user from calling admin-only endpoints? — expected `backend/app/api/deps.py` · `get_current_active_superuser`; got `backend/tests/api/routes/test_users.py:508-521` (test_delete_user_without_privileges), `backend/tests/api/routes/test_users.py:26-34` (test_get_users_normal_user_me), `backend/tests/api/routes/test_users.py:117-129` (test_get_existing_user_permissions_error), `backend/tests/api/routes/test_users.py:132-143` (test_get_non_existing_user_permissions_error), `frontend/src/routes/_layout/admin.tsx:59-74` (Admin)
- **r14** What checks are made when users change their own password? — expected `backend/app/api/routes/users.py` · `update_password_me`; got `backend/app/core/config.py:68-77` (Settings._check_default_secret), `frontend/src/components/UserSettings/ChangePassword.tsx:20-37` (formSchema), `frontend/tests/user-settings.spec.ts:147-204` (user-settings.spec.ts), `frontend/src/components/UserSettings/ChangePassword.tsx:41-144` (ChangePassword), `frontend/tests/user-settings.spec.ts:99-156` (user-settings.spec.ts)
- **r18** How long are access tokens valid by default, and where is that configured? — expected `backend/app/core/config.py` · `Settings`; got `backend/app/api/routes/login.py:23-42` (login_access_token), `frontend/src/client/sdk.gen.ts:22-38` (LoginService.loginAccessToken), `backend/tests/api/routes/test_login.py:16-25` (test_get_access_token), `backend/app/core/security.py:22-26` (create_access_token), `backend/tests/api/routes/test_login.py:37-46` (test_use_access_token)

## Invalid citations

- **r21** mention `main.py:None-None` — file does not exist
