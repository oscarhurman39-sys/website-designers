# Why didn't my email arrive? — one command answers it

SendGrid saying `HTTP 202 (ACCEPTED)` does **not** mean delivered — it means
"accepted for processing". The message can still die afterwards, invisibly.
The test-email command now diagnoses (and in one case auto-fixes) every known
cause. Run, from the repo root:

```powershell
git pull origin claude/cold-email-sales-pipeline-nuq8h8
del pipeline\screenshots\*.png
venv\Scripts\python.exe run.py test-email YOUR_ADDRESS@example.com
```

**First check:** the output must start with `pipeline build: <hash>`. If that
line is missing, you are running an old build — none of the diagnostics below
exist in it. If a `YOUR CODE IS N COMMIT(S) BEHIND` banner prints, stop and
`git pull`.

## Reading the verdict

| Output | Meaning | What to do |
|---|---|---|
| `FOUND IT: ... suppression list(s)` | An earlier bounce put this address on SendGrid's suppression list; every send since was silently dropped after the 202. | Nothing — the run auto-clears it (test addresses only) and this very email should arrive. |
| `WARNING: From and To are the SAME mailbox` | Gmail treats third-party mail "from yourself" as spoofing and discards it: no inbox, no spam, no bounce. | Send the test to a different address you own, or fix the From (below). |
| `WARNING: the From address is a free mailbox` | SendGrid can't DKIM-sign gmail.com/outlook.com, so DMARC fails at Gmail and mail is spam-foldered or dropped. | SendGrid dashboard → Settings → Sender Authentication → **Authenticate Your Domain** (needs a domain you own + 3 DNS records), then set `SENDGRID_FROM_EMAIL=you@yourdomain` in `.env`. This is the permanent fix for cold-email deliverability. |
| `status=not_delivered` | Gmail/receiver rejected it after acceptance. | Same fix as above (domain authentication). |
| `status=delivered` | Gmail took it — it IS in the mailbox somewhere. | Search All Mail for the subject; check Spam and the Promotions tab. |
| `PREVIEW IS NOT PUBLIC (HTTP 401/403)` | Vercel deployment protection is walling off the preview site — recipients clicking the link get a Vercel login page. The pipeline now tries to disable this automatically at deploy time. | If it still prints: Vercel dashboard → project → Settings → Deployment Protection → Vercel Authentication → Disabled. |
| `(Email Activity API not enabled ...)` | Your SendGrid plan doesn't expose delivery status via API. | app.sendgrid.com → Activity → search the recipient; the status column is the same verdict. |

Harmless noise you can ignore: the `HF drafting failed ... getaddrinfo` line —
your network can't reach Hugging Face, so email/website copy uses the built-in
fallback templates by design.
