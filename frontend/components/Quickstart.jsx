"use client";

import { useState } from "react";
import { motion } from "framer-motion";

const SNIPPETS = [
  {
    label: "Pull & run",
    code: "docker run -p 8080:80 portside/storefront:latest",
  },
  {
    label: "docker-compose.yml",
    code: `services:
  web:
    image: portside/storefront:latest
    ports:
      - "8080:80"
    environment:
      NODE_ENV: production`,
  },
  {
    label: "Compose up",
    code: "docker compose up -d",
  },
];

function Snippet({ label, code }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard API unavailable — fail silently, code is still selectable.
    }
  }

  return (
    <div className="panel overflow-hidden">
      <div className="flex items-center justify-between border-b border-yard-700 bg-yard-800/50 px-4 py-2.5">
        <span className="font-mono text-xs text-ink-muted">{label}</span>
        <button
          onClick={copy}
          className="rounded-md border border-yard-700 px-2.5 py-1 font-mono text-xs text-ink-muted transition hover:border-haul hover:text-ink"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto px-4 py-4 font-mono text-sm leading-relaxed text-run">
        {code}
      </pre>
    </div>
  );
}

export default function Quickstart() {
  return (
    <section id="quickstart" className="container-px mx-auto max-w-6xl py-24">
      <p className="eyebrow mb-3">Developer docs</p>
      <h2 className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
        Quickstart
      </h2>
      <p className="mt-3 max-w-lg text-ink-muted">
        Three commands, start to finish. Copy, paste, done.
      </p>

      <motion.div
        initial={{ opacity: 0, y: 16 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true }}
        transition={{ duration: 0.5 }}
        className="mt-10 grid gap-5 lg:grid-cols-3"
      >
        {SNIPPETS.map((s) => (
          <Snippet key={s.label} {...s} />
        ))}
      </motion.div>
    </section>
  );
}
