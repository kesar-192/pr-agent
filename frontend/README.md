# Portside

Dockerized-environment-as-a-service marketing site, built with Next.js 14 (App
Router), Tailwind CSS, and Framer Motion. The site is itself packaged as a
Docker image, as a live demo of the product it's selling.

## Local development

```bash
npm install
npm run dev
# → http://localhost:3000
```

## Run it the way we sell it (one command)

```bash
docker compose up -d
# → http://localhost:8080
```

Or without Compose:

```bash
docker build -t portside/site .
docker run -p 8080:3000 portside/site
```

## Project structure

```
app/                 Next.js App Router pages, layout, global styles
components/          Section components (Hero, Services, Terminal, etc.)
public/              Static assets
Dockerfile           Multi-stage build → minimal standalone runtime image
docker-compose.yml   One-command run configuration
```
