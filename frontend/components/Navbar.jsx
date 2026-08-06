"use client";

const LINKS = [
  { href: "#services", label: "Services" },
  { href: "#how-it-works", label: "How it works" },
  { href: "#quickstart", label: "Quickstart" },
  { href: "#contact", label: "Contact" },
];

export default function Navbar() {
  return (
    <header className="sticky top-0 z-50 border-b border-yard-700/60 bg-yard-950/80 backdrop-blur-md">
      <nav className="container-px mx-auto flex h-16 max-w-6xl items-center justify-between">
        <a href="#" className="flex items-center gap-2 font-display text-lg font-semibold tracking-tight text-ink">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-haul/15 text-haul shadow-glow-blue">
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 fill-current">
              <rect x="3" y="3" width="8" height="8" rx="1" />
              <rect x="13" y="3" width="8" height="8" rx="1" />
              <rect x="3" y="13" width="8" height="8" rx="1" />
            </svg>
          </span>
          Portside
        </a>

        <div className="hidden items-center gap-8 md:flex">
          {LINKS.map((l) => (
            <a
              key={l.href}
              href={l.href}
              className="text-sm text-ink-muted transition hover:text-ink"
            >
              {l.label}
            </a>
          ))}
        </div>

        <a
          href="#contact"
          className="rounded-lg border border-haul/40 bg-haul/10 px-4 py-2 text-sm font-medium text-ink shadow-glow-blue transition hover:bg-haul/20"
        >
          Request a build
        </a>
      </nav>
    </header>
  );
}
