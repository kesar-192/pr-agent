"use client";

import { useState } from "react";
import { motion } from "framer-motion";

export default function ContactForm() {
  const [status, setStatus] = useState("idle"); // idle | sending | sent

  function handleSubmit(e) {
    e.preventDefault();
    setStatus("sending");
    // Wire this up to your API route / email provider of choice.
    window.setTimeout(() => setStatus("sent"), 900);
  }

  return (
    <section id="contact" className="container-px mx-auto max-w-6xl py-24">
      <div className="panel shadow-glow-blue grid gap-10 p-8 md:grid-cols-2 md:p-12">
        <div>
          <p className="eyebrow mb-3">Custom builds</p>
          <h2 className="font-display text-3xl font-semibold tracking-tight text-ink">
            Tell us what you need containerized
          </h2>
          <p className="mt-4 max-w-sm text-sm leading-relaxed text-ink-muted">
            Send over your stack or repo and we&apos;ll scope a Dockerfile
            and Compose architecture built for it — usually back to you
            within one business day.
          </p>
        </div>

        <div>
          {status === "sent" ? (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className="flex h-full items-center rounded-lg border border-run/30 bg-run/5 p-6 text-sm text-run"
            >
              Request sent. We&apos;ll reply from hello@portside.dev.
            </motion.div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="name" className="mb-1.5 block text-xs text-ink-muted">
                  Name
                </label>
                <input
                  id="name"
                  required
                  type="text"
                  className="w-full rounded-lg border border-yard-700 bg-yard-950 px-3.5 py-2.5 text-sm text-ink outline-none transition focus:border-haul"
                />
              </div>
              <div>
                <label htmlFor="email" className="mb-1.5 block text-xs text-ink-muted">
                  Work email
                </label>
                <input
                  id="email"
                  required
                  type="email"
                  className="w-full rounded-lg border border-yard-700 bg-yard-950 px-3.5 py-2.5 text-sm text-ink outline-none transition focus:border-haul"
                />
              </div>
              <div>
                <label htmlFor="details" className="mb-1.5 block text-xs text-ink-muted">
                  What are we containerizing?
                </label>
                <textarea
                  id="details"
                  required
                  rows={3}
                  className="w-full resize-none rounded-lg border border-yard-700 bg-yard-950 px-3.5 py-2.5 text-sm text-ink outline-none transition focus:border-haul"
                />
              </div>
              <button
                type="submit"
                disabled={status === "sending"}
                className="w-full rounded-lg bg-haul px-5 py-3 text-sm font-medium text-white shadow-glow-blue transition hover:bg-haul-dim disabled:opacity-60"
              >
                {status === "sending" ? "Sending…" : "Request a build"}
              </button>
            </form>
          )}
        </div>
      </div>
    </section>
  );
}
