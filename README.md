# linuxkurssi-chat

A web chat with a language model that runs on my own cPouta server. Week 5 of the Linux course.

- Chat and documentation: http://195.148.20.88/viikko5/
- Running version: http://195.148.20.88/viikko5/health (the Git commit SHA of the deploy)

```
Browser -> Nginx :80 -> Unix socket -> Gunicorn + Flask -> Ollama (127.0.0.1:11434)
                                                         -> MySQL (127.0.0.1:3306)
```

Every push and pull request runs the linter and the tests. A push to `main` that passes is deployed to the server by GitHub Actions.

The application code is based on the course material
([LLM-chat cPoutaan](https://www.villemajava.com/linuxcicd/ollama)), adapted for MySQL 8.4, `gemma3:4b` and Python 3.14.

## Files

| Path | What |
|---|---|
| `app.py` | Flask app: `/`, `/health`, `/api/chat` (streams the reply), `/api/history/<id>` |
| `db.py` | MySQL storage. Without `DB_NAME` the messages are kept in memory. |
| `test_app.py` | Tests. Ollama and the database are mocked. |
| `templates/`, `static/` | The page and the chat UI |
| `deploy/nginx/chatapp.conf` | Nginx snippet for `/viikko5/` |
| `deploy/systemd/chatapp.service` | systemd unit, runs as user `chatapp` |
| `deploy/sql/schema.sql` | The `messages` table |
| `deploy/chatapp.env.example` | Template for the env file on the server |
| `scripts/deploy.sh` | Run on the server by the workflow |
| `.github/workflows/ci-cd.yml` | The pipeline |

## Run it locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows Git Bash: source .venv/Scripts/activate
pip install -r requirements-dev.txt
ruff check .
pytest
flask --app app run --debug      # http://127.0.0.1:5000
```

Without Ollama on your machine the page works, but the chat shows "The language model did not respond".

## Set up the server (once)

These steps were done by hand on server A (Ubuntu, MySQL 8.4 and Nginx already installed).

1. **Ollama and the model**

   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ollama pull gemma3:4b
   ```

   Ollama listens only on `127.0.0.1:11434` by default.

2. **Service user and database**

   ```bash
   sudo useradd --system --no-create-home --shell /usr/sbin/nologin chatapp
   ```

   Generate a password on the server (`openssl rand -hex 24`), then in MySQL:

   ```sql
   CREATE DATABASE chatapp CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'chatapp'@'localhost' IDENTIFIED BY '<generated password>';
   GRANT SELECT, INSERT ON chatapp.* TO 'chatapp'@'localhost';
   ```

3. **Env file**: copy `deploy/chatapp.env.example` to `/etc/chatapp/chatapp.env`, fill in the password, then:

   ```bash
   sudo chown root:root /etc/chatapp/chatapp.env
   sudo chmod 600 /etc/chatapp/chatapp.env
   ```

   systemd reads the file as root, so the app gets the values without being able to read the file.

4. **App directory**, owned by the deploy user:

   ```bash
   sudo mkdir -p /opt/chatapp/app
   sudo chown -R ubuntu:ubuntu /opt/chatapp
   ```

5. **Nginx**: create an empty `/etc/nginx/snippets/chatapp.conf` and add one line inside the existing site's `server { }` block in `/etc/nginx/sites-available/default`:

   ```nginx
   include snippets/chatapp.conf;
   ```

   Then `sudo nginx -t && sudo systemctl reload nginx`. The deploy fills in the snippet.

6. **Deploy key**: make a key pair only for GitHub Actions and add the public key to `~ubuntu/.ssh/authorized_keys`:

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/cpouta_deploy -C "github-actions-deploy" -N ""
   ```

7. **GitHub secrets** (Settings, Secrets and variables, Actions):

   | Secret | Value |
   |---|---|
   | `CPOUTA_HOST` | Server IP |
   | `CPOUTA_USER` | `ubuntu` |
   | `CPOUTA_SSH_KEY` | The private deploy key |
   | `CPOUTA_KNOWN_HOSTS` | `<ip> ssh-ed25519 AAAA...` from the server's `/etc/ssh/ssh_host_ed25519_key.pub` |

## How a deploy works

On a push to `main`, after the tests pass, the `deploy` job:

1. copies the code to `/opt/chatapp/app` with `rsync --delete` (`REVISION` and `static/gifs` are left alone),
2. runs `scripts/deploy.sh <commit SHA>` on the server, which installs the Python packages into `/opt/chatapp/venv`, writes the SHA to `REVISION`, applies the schema, installs the Nginx snippet (tested with `nginx -t`, the old one is restored if it fails), installs the systemd unit and restarts only `chatapp`,
3. checks that `/health` returns the new SHA, first through the socket and then from the public address.

If a test fails, the deploy job is skipped and the server keeps the old version.

## Secrets

No passwords or SSH keys are in this repository. The database password exists only in `/etc/chatapp/chatapp.env` on the server, and the SSH key only in GitHub secrets. `.gitignore` excludes `.env`, `*.env` and key files. The gifs on the page are not mine, so they are not in the repository either.
