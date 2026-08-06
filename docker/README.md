# PR-Agent Prompting Agent — Docker

## Quick Start

```bash
# 1. Set API keys (edit and uncomment your provider)
cat > .env << 'EOF'
GITHUB_TOKEN=ghp_...
GROQ_API_KEY=gsk_...
# OPENAI_API_KEY=sk-proj-...
# ANTHROPIC_API_KEY=sk-ant-...
EOF

# 2. Build and run
docker compose -f docker-compose.prompting-agent.yml up --build
```

Open **http://localhost:3000** in your browser (Next.js web UI). The backend API
(and a static copy of the UI) is also served directly at **http://localhost:8090**.

The stack starts three services:

| Service | Role | Host port |
|---|---|---|
| `prompting-frontend` | Next.js web UI; proxies `/api/*` and `/streams/*` to the backend | `3000` |
| `prompting-agent` | FastAPI + SSE backend (`pr_agent.servers.prompting_server`) | `8090` |
| `mongo-db` | MongoDB persistence + 60 s host JSON backup (internal only) | — |

Every session is written to MongoDB (`prompting_agent.sessions`) as it happens,
so history survives container restarts. Inside the compose network the agent
receives `MONGO_URI=mongodb://mongo-db:27017` automatically; if that is empty or
unreachable, sessions fall back to in-memory storage.

---

## MongoDB persistence & the 60-second JSON backup

- Every session is written to MongoDB (`prompting_agent.sessions`) as it
  happens, so the history is durable even if the app container restarts.
- The `mongo-db` container **dumps the history to a JSON file on your host**
  every 60 seconds:

  ```
  ./mongo_backups/history_backup.json
  ```

- **Container fails?** The host JSON survives. On the next `docker compose up`,
  the mongo container restores that JSON into a fresh MongoDB automatically
  (upsert by `session_id`, so nothing is lost or duplicated).
- **No JSON file yet?** MongoDB just creates a new database, and the backup
  file appears once the first history is written and the next 60s dump runs.

Tune the interval or the backup path via env vars:

```bash
BACKUP_INTERVAL_SECONDS=30 \
  docker compose -f docker-compose.prompting-agent.yml up
```

To wipe history, stop the stack and delete `./mongo_backups/` plus the volume:

```bash
docker compose -f docker-compose.prompting-agent.yml down -v
rm -rf mongo_backups
```

---

## Passing Secrets

**Option A — `.env` file** (recommended):

```bash
echo "GITHUB_TOKEN=ghp_..." >> .env
echo "GROQ_API_KEY=gsk_..." >> .env
docker compose -f docker-compose.prompting-agent.yml up
```

**Option B — inline environment variables**:

```bash
GITHUB_TOKEN=ghp_... GROQ_API_KEY=gsk_... \
  docker compose -f docker-compose.prompting-agent.yml up
```

**Option C — plain `docker run`** (no compose):

```bash
# Build the image first (see "Building Without docker-compose" below)
docker run -d --name pr-agent-prompting \
  -p 8090:8090 \
  --env-file .env \
  pr-agent/prompting-agent:latest
```

Without the compose network there is no `mongo-db` service, so unless you pass a
reachable `MONGO_URI` the agent stores sessions **in memory only** (lost on
restart). To keep persistence, run a MongoDB container with the same
backup/restore entrypoint and point the agent at it:

```bash
docker network create pr-agent-net

docker run -d --name pr-agent-mongo \
  --network pr-agent-net \
  -v "$PWD/mongo_backups:/backups" \
  -v "$PWD/docker/mongo/entrypoint.sh:/scripts/entrypoint.sh:ro" \
  -e MONGO_DATABASE=prompting_agent \
  -e MONGO_COLLECTION=sessions \
  --entrypoint /scripts/entrypoint.sh \
  mongo:7

docker run -d --name pr-agent-prompting \
  --network pr-agent-net \
  -p 8090:8090 \
  --env-file .env \
  -e MONGO_URI=mongodb://pr-agent-mongo:27017 \
  pr-agent/prompting-agent:latest
```

The UI is a separate Next.js container; for the full UI use the compose stack,
or run the frontend locally against the backend at `http://localhost:8090`.

---

## Required Secrets

| Variable | Purpose |
|---|---|
| `GITHUB_TOKEN` | Fetch PR diffs and metadata (required) |
| `GROQ_API_KEY` | LLM: groq/llama-3.3-70b-versatile (default model) |
| `OPENAI_API_KEY` | LLM: gpt-4o / gpt-4o-mini |
| `ANTHROPIC_API_KEY` | LLM: claude-sonnet-4 |

## Managing the Stack

```bash
# Start in the background (build images on first run)
docker compose -f docker-compose.prompting-agent.yml up -d --build

# Stream logs from all services (or add <service> to filter)
docker compose -f docker-compose.prompting-agent.yml logs -f

# Restart after editing config / prompts
docker compose -f docker-compose.prompting-agent.yml restart

# Stop without deleting the mongo volume or host backup
docker compose -f docker-compose.prompting-agent.yml stop

# Full teardown (removes the mongo volume; host backup in ./mongo_backups survives)
docker compose -f docker-compose.prompting-agent.yml down
```

## Building Without docker-compose

```bash
docker build -f docker/Dockerfile \
  --target prompting_agent \
  -t pr-agent/prompting-agent:latest .
```

## Verify It Works

```bash
# Web UI (Next.js) and backend both serve the health-check page
curl -s http://localhost:3000/ | head -3     # UI is up
curl -s http://localhost:8090/ | head -3     # backend is up → <!DOCTYPE html> ...

# Check MongoDB persistence is connected (look for "MongoSessionStore connected")
docker compose -f docker-compose.prompting-agent.yml logs prompting-agent | grep -i mongo

# Create a session — hit the backend directly on :8090, or the same path via the
# frontend proxy on :3000 (http://localhost:3000/api/v1/sessions)
curl -s -X POST http://localhost:8090/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"pr_url": "https://github.com/owner/repo/pull/123"}' | jq .
# → {"session_id": "...", "status": "ready"}

# Confirm the backup file appears on the host within ~60s
ls -la ./mongo_backups/history_backup.json
```
