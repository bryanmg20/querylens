/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Los valores viven en app/globals.css como variables CSS: un solo lugar
      // para los tokens del tema oscuro.
      colors: {
        ground: "var(--ground)",
        surface: "var(--surface)",
        raised: "var(--raised)",
        line: "var(--border)",
        rule: "var(--rule)",
        control: "var(--control)",
        fg: "var(--text)",
        muted: "var(--muted)",
        accent: "var(--accent)",
        positive: "var(--positive)",
        negative: "var(--negative)",
        warning: "var(--warning)",
      },
      fontFamily: {
        sans: ["var(--font-public-sans)", "Inter", "system-ui", "sans-serif"],
        mono: ["var(--font-plex-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      borderRadius: {
        sm: "4px",
        DEFAULT: "6px",
      },
    },
  },
  plugins: [],
};
