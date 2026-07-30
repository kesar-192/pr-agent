/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,jsx}", "./components/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        yard: {
          950: "#0A0D12", // base background — port yard at night
          900: "#12161D", // panel surface
          800: "#1A1F29", // raised panel / card
          700: "#242B38", // borders, dividers
        },
        haul: {
          DEFAULT: "#2496ED", // Docker blue — primary accent
          dim: "#1B72B8",
        },
        signal: {
          DEFAULT: "#FFB020", // sodium-lamp amber — live/status glow only
        },
        run: {
          DEFAULT: "#34D399", // running-container green
        },
        ink: {
          DEFAULT: "#E7EBF0", // primary text
          muted: "#8B95A1", // secondary text
          faint: "#576174", // tertiary / labels
        },
      },
      fontFamily: {
        display: ["var(--font-display)", "sans-serif"],
        body: ["var(--font-body)", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      boxShadow: {
        "glow-blue": "0 0 0 1px rgba(36,150,237,0.25), 0 0 24px rgba(36,150,237,0.15)",
        "glow-amber": "0 0 0 1px rgba(255,176,32,0.3), 0 0 20px rgba(255,176,32,0.2)",
        "glow-run": "0 0 0 1px rgba(52,211,153,0.3), 0 0 20px rgba(52,211,153,0.2)",
      },
      backgroundImage: {
        "grid-fade":
          "linear-gradient(to bottom, rgba(10,13,18,0) 0%, #0A0D12 90%), radial-gradient(ellipse at top, rgba(36,150,237,0.08), transparent 60%)",
      },
      keyframes: {
        blink: { "0%, 49%": { opacity: 1 }, "50%, 100%": { opacity: 0 } },
        "pulse-glow": {
          "0%, 100%": { opacity: 0.6 },
          "50%": { opacity: 1 },
        },
      },
      animation: {
        blink: "blink 1s step-start infinite",
        "pulse-glow": "pulse-glow 2.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
