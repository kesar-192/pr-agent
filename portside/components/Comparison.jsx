"use client";

import { motion } from "framer-motion";

const TRADITIONAL = [
  "Install the right language runtime version",
  "Install and configure the database locally",
  "Resolve dependency and version conflicts",
  "Set environment variables by hand",
  "Debug \"works on my machine\" issues",
];

const PORTSIDE = ["docker run -p 8080:80 my-service"];

export default function Comparison() {
  return (
    <section className="container-px mx-auto max-w-6xl py-24">
      <p className="eyebrow mb-3">The difference</p>
      <h2 className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
        Same result, one fewer afternoon
      </h2>

      <div className="mt-12 grid gap-6 lg:grid-cols-2">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="panel border-yard-700 p-7 opacity-80"
        >
          <p className="font-mono text-xs uppercase tracking-[0.15em] text-ink-faint">
            Traditional setup
          </p>
          <ul className="mt-5 space-y-3">
            {TRADITIONAL.map((step) => (
              <li key={step} className="flex items-start gap-3 text-sm text-ink-muted">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-ink-faint" />
                {step}
              </li>
            ))}
          </ul>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.1 }}
          className="panel shadow-glow-blue border-haul/40 p-7"
        >
          <p className="font-mono text-xs uppercase tracking-[0.15em] text-haul">
            The Portside way
          </p>
          <div className="mt-5 rounded-lg border border-yard-700 bg-yard-950 p-4 font-mono text-sm text-run">
            {PORTSIDE.map((line) => (
              <p key={line}>$ {line}</p>
            ))}
          </div>
          <p className="mt-4 text-sm text-ink-muted">
            One line. Nothing else to install, resolve, or configure.
          </p>
        </motion.div>
      </div>
    </section>
  );
}
