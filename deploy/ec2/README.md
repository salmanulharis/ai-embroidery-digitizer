# Go live: AWS EC2 + Namecheap subdomain + GitHub Actions (deploy on tag)

**OS:** Ubuntu Server 24.04 LTS (x86_64). Supported until 2029, ships Python 3.12, and is the exact
system the setup is tested on.

**Where to save values:** GitHub repo → **Settings → Secrets and variables → Actions**.
- 🟦 **Variable:** *Variables* tab → **New repository variable**.
- 🟥 **Secret:** *Secrets* tab → **New repository secret**.

Add each value at the step where it appears. Don't make one combined env file; every value is its own entry.

---

## 1. AWS safety net
Billing → **Budgets** → **Create budget** → **Zero spend budget** → your email → Create.

## 2. Launch EC2 (region: Europe (Stockholm) eu-north-1)
EC2 → **Launch instance**:
- **Name:** `embroidery`.
- **AMI:** **Ubuntu Server 24.04 LTS**, 64-bit (x86).
- **Instance type:** **t3.small** (Free tier eligible; use t3.micro if t3.small isn't eligible).
- **Key pair:** **Create new**, name `embroidery-key`, type ED25519, `.pem`. It downloads to your PC.
- **Network → Edit → Create security group** `embroidery-web` with these inbound rules:
  - SSH 22 from **Anywhere 0.0.0.0/0**. GitHub's deploy runners need this; login is key-only and fail2ban is installed.
  - HTTP 80 from Anywhere.
  - HTTPS 443 from Anywhere.
- **Storage:** 20 GiB **gp3**.
- **Launch.**

## 3. Fixed IP
EC2 → **Elastic IPs** → **Allocate** → select it → **Actions → Associate** → instance `embroidery`.

| Save | Name | Value |
|---|---|---|
| 🟦 | `EC2_HOST` | the Elastic IP, e.g. `13.48.10.20` |
| 🟦 | `EC2_USER` | `ubuntu` |

## 4. Subdomain on Namecheap
Namecheap → **Domain List** → your domain → **Manage** → **Advanced DNS** → **Add New Record**:

| Type | Host | Value | TTL |
|---|---|---|---|
| A Record | `embroidery` | your Elastic IP | Automatic |

Click ✓ to save. Check from your PC (wait until it shows the IP):
```powershell
nslookup embroidery.yourdomain.com
```
(If the domain's nameservers aren't *Namecheap BasicDNS*, add the record at your DNS provider instead.)

| Save | Name | Value |
|---|---|---|
| 🟦 | `APP_DOMAIN` | `embroidery.yourdomain.com` |
| 🟦 | `LETSENCRYPT_EMAIL` | your email (HTTPS certificate notices) |

## 5. Deploy key for GitHub Actions (PowerShell on your PC)
```powershell
# your AWS login key: make it private
Move-Item $HOME\Downloads\embroidery-key.pem $HOME\.ssh\embroidery-key.pem
icacls $HOME\.ssh\embroidery-key.pem /inheritance:r /grant:r "$($env:USERNAME):R"

# a separate key only for GitHub deployments
ssh-keygen -t ed25519 -f $HOME\.ssh\github-deploy -N '""' -C "github-actions-deploy"
Get-Content $HOME\.ssh\github-deploy.pub | ssh -i $HOME\.ssh\embroidery-key.pem ubuntu@YOUR_ELASTIC_IP "cat >> ~/.ssh/authorized_keys"

# copy the private key, then paste it as the secret below
Get-Content $HOME\.ssh\github-deploy -Raw | Set-Clipboard

# server identity (stops anyone pretending to be your server)
ssh-keyscan -t ed25519 YOUR_ELASTIC_IP
```
Answer `yes` when asked about the fingerprint.

| Save | Name | Value |
|---|---|---|
| 🟥 | `EC2_SSH_PRIVATE_KEY` | clipboard: the whole private key, including the `BEGIN`/`END` lines |
| 🟦 | `EC2_KNOWN_HOSTS` | the full line printed by `ssh-keyscan` (`13.48.10.20 ssh-ed25519 AAAA…`) |

## 6. Accounts (required)
The app has its own sign-in. There is no public sign-up: the Super Admin creates every
account under **Account menu → Manage users**.

| Save | Name | Value |
|---|---|---|
| 🟥 | `SUPER_ADMIN_EMAIL` | the Super Admin's email |
| 🟥 | `SUPER_ADMIN_PASSWORD` | at least 10 characters (avoid `"` `'` and `\`) |
| 🟥 | `APP_SECRET_KEY` | recommended: a long random string, e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Encrypts the users' saved AI keys. Never change it afterwards, or saved AI keys can no longer be read (users then add them again). Without it a random key is created on the server in `/var/lib/embroidery/secret.key` |
| 🟦 | `SESSION_DAYS` | how long "Keep me signed in" lasts, default 30 |

The deployment stops if the two `SUPER_ADMIN_*` secrets are missing. The account is created
on the first start only; changing the secrets later does not change an existing account.
Forgot the Super Admin password? On the server:
```bash
sudo systemd-run --pty --quiet --uid=embroidery -p EnvironmentFile=/etc/embroidery/embroidery.env --working-directory=/opt/embroidery /opt/embroidery/.venv/bin/python -m embroidery_app.auth reset-password you@example.com
```

`SITE_LOGIN` at the top of `.github/workflows/deploy.yml` can still put an extra Caddy
password (`BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD`) in front of the whole site; keep it
`"off"` - the app's own accounts replace it.

## 7. Optional settings (only add what you need)
| Save | Name | Default / example |
|---|---|---|
| 🟦 | `MAX_UPLOAD_MB` | 40 |
| 🟦 | `RASTER_MAX_SIDE_PX` | automatic (2000 on t3.small, 1400 on t3.micro) |
| 🟦 | `RASTER_PX_PER_MM` | 12 |
| 🟦 | `AI_TIMEOUT_S` | 120 |
| 🟥 | `GEMINI_API_KEY` · `OPENAI_API_KEY` · `OPENROUTER_API_KEY` · `ANTHROPIC_API_KEY` | server-wide AI keys: **every signed-in user can use them**. Prefer each user adding their own key in AI settings (saved per account, encrypted) |
| 🟦 | `GEMINI_IMAGE_MODEL`, `GEMINI_VISION_MODEL`, `OPENAI_IMAGE_MODEL`, `OPENAI_VISION_MODEL`, `OPENROUTER_IMAGE_MODEL`, `OPENROUTER_VISION_MODEL`, `ANTHROPIC_VISION_MODEL` | empty = automatic |

## 8. Release (deploys only on tags)
```bash
git checkout main
git pull
git tag v1.0.0
git push origin v1.0.0
```
**GitHub → Actions → "Release & deploy"** runs these steps:
1. Runs the tests.
2. Checks the tag is on `main`.
3. Uploads that exact code to the server.
4. Installs or updates it and restarts.
5. Checks `https://APP_DOMAIN`.

The first run takes about 5 minutes; later ones take about 1 minute. Then open `https://embroidery.yourdomain.com`.

Pushing commits or branches **never** deploys. Only `v*` tags on `main` do.

## Rollback and maintenance
| Need | Do |
|---|---|
| New version | `git tag v1.0.1 && git push origin v1.0.1` |
| A release fails to start | Automatic: the server restores the previous release and the workflow turns red |
| Go back to an older release | Actions → open that tag's run → **Re-run all jobs** |
| Change a setting or key | Edit the variable or secret, then re-run the latest release run |
| Logs | `ssh -i $HOME\.ssh\embroidery-key.pem ubuntu@IP`, then `sudo journalctl -u embroidery -f` |
| Save credits | EC2 → Stop the instance (same IP when started again) |
