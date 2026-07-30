"use client";

import { motion } from "framer-motion";

const SERVICES = [
  {
    tag: "Pre-packaged",
    title: "Containerized web apps",
    body: "Full applications — frontend, backend, database — shipped as one pullable image. No manual setup, no config drift.",
  },
  {
    tag: "Custom-built",
    title: "Dockerfile & Compose architecture",
    body: "Multi-service systems designed around your stack, with a Compose file that wires everything together correctly the first time.",
  },
  {
    tag: "Self-hosted",
    title: "One-command local hosting",
    body: "Run the exact same environment on your laptop or your server. `docker run` is the whole install process.",
  },
  {
    tag: "Automated",
    title: "Production-ready CI/CD pipelines",
    body: "Build, test, and publish container images automatically on every push — ready to deploy the moment they pass.",
  },
];

export default function Services() {
  return (
    <section id="services" className="container-px mx-auto max-w-6xl py-24">
      <p className="eyebrow mb-3">What we ship</p>
      <h2 className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
        Four ways to hand off a running system
      </h2>

      <div className="mt-12 grid gap-5 sm:grid-cols-2">
        {SERVICES.map((s, i) => (
          <motion.div
            key={s.title}
            initial={{ opacity: 0, y: 16 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }}
            transition={{ duration: 0.5, delay: i * 0.06 }}
            className="panel group p-6 transition hover:border-haul/50"
          >
            <span className="font-mono text-xs uppercase tracking-[0.15em] text-signal">
              {s.tag}
            </span>
            <h3 className="mt-3 font-display text-xl font-semibold text-ink">
              {s.title}
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-ink-muted">
              {s.body}
            </p>
          </motion.div>
        ))}
      </div>
    </section>
  );
}
