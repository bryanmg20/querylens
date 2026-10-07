/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#F3F5F4",
        surface: "#FFFFFF",
        ink: "#12161A",
        graphite: {
          600: "#4B545C",
          300: "#C4CBCE",
          200: "#DDE2E3",
        },
        signal: {
          DEFAULT: "#0E7C86",
          soft: "#E4F1F1",
        },
        alert: {
          DEFAULT: "#B23B3B",
          soft: "#F7E9E7",
        },
      },
      fontFamily: {
        sans: ["var(--font-plex-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-plex-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      borderRadius: {
        sm: "3px",
        DEFAULT: "4px",
      },
    },
  },
  plugins: [],
};
