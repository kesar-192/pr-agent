export default function Footer() {
  return (
    <footer className="border-t border-yard-700/60">
      <div className="container-px mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 py-8 sm:flex-row">
        <p className="font-mono text-xs text-ink-faint">
          © {new Date().getFullYear()} Portside. Shipped in a container.
        </p>
        <p className="font-mono text-xs text-ink-faint">
          status: <span className="text-run">all systems running</span>
        </p>
      </div>
    </footer>
  );
}
