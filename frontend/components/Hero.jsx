"use client";

import { motion } from "framer-motion";
import Terminal from "./Terminal";

export default function Hero() {
  return (
    <section className="container-px relative mx-auto grid max-w-6xl gap-14 pb-24 pt-20 lg:grid-cols-[1.1fr_1fr] lg:items-center lg:pt-28">
      <div>
        <motion.p
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="eyebrow mb-5"
        >
          Dockerized Environment-as-a-Service
        </motion.p>

        <motion.h1
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.05 }}
          className="font-display text-4xl font-semibold leading-[1.08] tracking-tight text-ink sm:text-5xl lg:text-6xl"
        >
          Ship the whole
          <br />
          environment, not just
          <br />
          the <span className="text-haul">code</span>.
        </motion.h1>

        <motion.p
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.1 }}
          className="mt-6 max-w-md text-lg text-ink-muted"
        >
          No dependency hunting, no "works on my machine." Pull a container
          that&apos;s already configured, tested, and production-ready — and
          run it anywhere in seconds.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.15 }}
          className="mt-9 flex flex-wrap items-center gap-4"
        >
          <a
            href="#quickstart"
            className="rounded-lg bg-haul px-5 py-3 text-sm font-medium text-white shadow-glow-blue transition hover:bg-haul-dim"
          >
            Get the quickstart
          </a>
          <a
            href="#services"
            className="rounded-lg border border-yard-700 px-5 py-3 text-sm font-medium text-ink-muted transition hover:border-ink-faint hover:text-ink"
          >
            See what we ship
          </a>
        </motion.div>
      </div>

      <motion.div
        initial={{ opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.6, delay: 0.2 }}
        className="lg:justify-self-end"
      >
        <Terminal />
      </motion.div>
    </section>
  );
}
