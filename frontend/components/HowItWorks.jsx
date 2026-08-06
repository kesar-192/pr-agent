"use client";

import { motion } from "framer-motion";

const STEPS = [
  {
    n: "01",
    title: "Select or request a solution",
    body: "Pick from our catalog of pre-built images, or describe the stack you need and we'll architect it.",
  },
  {
    n: "02",
    title: "Pull the pre-configured image",
    body: "One `docker pull` fetches the exact, tested environment — dependencies, config, and all.",
  },
  {
    n: "03",
    title: "Run & access it anywhere",
    body: "Start it locally or on your own server with a single command. Same container, same behavior, every time.",
  },
];

export default function HowItWorks() {
  return (
    <section id="how-it-works" className="container-px mx-auto max-w-6xl py-24">
      <p className="eyebrow mb-3">Workflow</p>
      <h2 className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
        How it works
      </h2>

      <div className="mt-12 grid gap-8 md:grid-cols-3">
        {STEPS.map((s, i) => (
          <motion.div
            key={s.n}
            initial={{ opacity: 0, y: 16 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }}
            transition={{ duration: 0.5, delay: i * 0.08 }}
            className="relative"
          >
            <span className="font-display text-5xl font-semibold text-yard-700">
              {s.n}
            </span>
            <h3 className="mt-4 font-display text-lg font-semibold text-ink">
              {s.title}
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-ink-muted">
              {s.body}
            </p>
            {i < STEPS.length - 1 && (
              <span className="pointer-events-none absolute right-[-1.5rem] top-6 hidden text-yard-700 md:block">
                &rarr;
              </span>
            )}
          </motion.div>
        ))}
      </div>
    </section>
  );
}
