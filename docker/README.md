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

Open **http://localhost:8090** in your browser.

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

**Option C — plain Docker run**:

```bash
docker run -d --name pr-agent-prompting \
  -p 8090:8090 \
  --env-file .env \
  pr-agent/prompting-agent:latest
```

---

## Required Secrets

| Variable | Purpose |
|---|---|
| `GITHUB_TOKEN` | Fetch PR diffs and metadata (required) |
| `GROQ_API_KEY` | LLM: groq/llama-3.3-70b-versatile (default model) |
| `OPENAI_API_KEY` | LLM: gpt-4o / gpt-4o-mini |
| `ANTHROPIC_API_KEY` | LLM: claude-sonnet-4 |

## Building Without docker-compose

```bash
docker build -f docker/Dockerfile \
  --target prompting_agent \
  -t pr-agent/prompting-agent:latest .
```

## Verify It Works

```bash
# Check health
curl -s http://localhost:8090/ | head -3
# → <!DOCTYPE html> ...

# Create a session
curl -s -X POST http://localhost:8090/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"pr_url": "https://github.com/owner/repo/pull/123"}' | jq .
# → {"session_id": "...", "status": "ready"}
```
