"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

const COMMANDS = [
  {
    label: "run",
    cmd: "docker run -p 8080:80 portside/storefront:latest",
    lines: [
      "Unable to find image 'portside/storefront:latest' locally",
      "latest: Pulling from portside/storefront",
      "a3ed95caeb02: Pull complete",
      "e5cd4c5bde83: Pull complete",
      "Digest: sha256:4f2a...b91c",
      "Status: Downloaded newer image for portside/storefront:latest",
    ],
    result: { id: "c7e1a9f2d8b4", status: "running", port: "8080" },
  },
  {
    label: "compose up",
    cmd: "docker compose up -d",
    lines: [
      "[+] Running 3/3",
      " ✔ Network portside_default   Created",
      " ✔ Container portside-api     Started",
      " ✔ Container portside-web     Started",
    ],
    result: { id: "portside-web", status: "running", port: "3000" },
  },
  {
    label: "ps",
    cmd: "docker ps --filter name=portside",
    lines: [
      "CONTAINER ID   IMAGE                        STATUS          PORTS",
      "c7e1a9f2d8b4   portside/storefront:latest   Up 4 seconds    0.0.0.0:8080->80/tcp",
    ],
    result: { id: "c7e1a9f2d8b4", status: "running", port: "8080" },
  },
];

export default function Terminal() {
  const [active, setActive] = useState(null);
  const [running, setRunning] = useState(false);

  function play(index) {
    if (running) return;
    setRunning(true);
    setActive(index);
    window.setTimeout(() => setRunning(false), COMMANDS[index].lines.length * 220 + 300);
  }

  const preset = active !== null ? COMMANDS[active] : null;

  return (
    <div className="panel shadow-glow-blue w-full max-w-xl overflow-hidden">
      {/* title bar */}
      <div className="flex items-center justify-between border-b border-yard-700 bg-yard-800/60 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-[#FF5F57]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#FEBC2E]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#28C840]" />
        </div>
        <span className="font-mono text-xs text-ink-faint">portside — zsh</span>
        <span className="w-12" />
      </div>

      {/* command chips */}
      <div className="flex flex-wrap gap-2 border-b border-yard-700 px-4 py-3">
        {COMMANDS.map((c, i) => (
          <button
            key={c.label}
            onClick={() => play(i)}
            className="rounded-full border border-yard-700 bg-yard-800 px-3 py-1 font-mono text-xs text-ink-muted transition hover:border-haul hover:text-ink focus-visible:border-haul"
          >
            {c.label}
          </button>
        ))}
      </div>

      {/* output */}
      <div className="h-64 overflow-y-auto px-4 py-4 font-mono text-sm leading-relaxed">
        {!preset && (
          <p className="text-ink-faint">
            <span className="text-run">$</span> select a command above to run it
            <span className="animate-blink">▊</span>
          </p>
        )}

        {preset && (
          <div>
            <p className="text-ink">
              <span className="text-run">$</span> {preset.cmd}
            </p>
            <AnimatePresence>
              {preset.lines.map((line, i) => (
                <motion.p
                  key={active + "-" + i}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.22, duration: 0.2 }}
                  className="text-ink-muted"
                >
                  {line}
                </motion.p>
              ))}
            </AnimatePresence>

            {!running && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: preset.lines.length * 0.22 + 0.15 }}
                className="mt-3 flex items-center gap-2 rounded-lg border border-run/30 bg-run/5 px-3 py-2 shadow-glow-run"
              >
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-pulse-glow rounded-full bg-run" />
                </span>
                <span className="text-xs text-run">
                  {preset.result.id} · listening on :{preset.result.port}
                </span>
              </motion.div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
