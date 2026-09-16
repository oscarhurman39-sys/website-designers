# caseywebsites.com landing page

Static one-page agency site plus /privacy and /terms, needed because Stripe
requires a real business website with a privacy policy (a parked domain is
not accepted) and because prospects look up the sender's domain.

Deploy (from repo root, Vercel token in .env):

    venv\Scripts\python.exe -c "import sys,json;sys.path.insert(0,'pipeline');from pathlib import Path;from utils import vercel_api;d=Path('agency-site');f={n:(d/n).read_text(encoding='utf-8') for n in ('index.html','privacy.html','terms.html')};f['vercel.json']=(d/'vercel.json').read_text();print(vercel_api.deploy_files('caseywebsites',f)['url'])"

Then in Vercel: project `caseywebsites` -> Settings -> Domains -> add
`caseywebsites.com` and `www.caseywebsites.com`. At Porkbun, replace the
ALIAS/CNAME records that point at uixie.porkbun.com with the records Vercel
shows (A 76.76.21.21 for the apex, CNAME cname.vercel-dns.com for www).
Keep every MX/TXT record (Zoho mail) untouched.
