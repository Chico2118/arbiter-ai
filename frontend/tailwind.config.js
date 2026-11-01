/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#0a0d12",
          900: "#0f141b",
          850: "#141a23",
          800: "#1a212c",
          700: "#263040",
          600: "#364357",
          500: "#56657c",
          400: "#8291a7",
          300: "#a9b5c6",
          200: "#cfd7e2",
          100: "#e8edf3",
        },
      },
      fontFamily: {
        sans: ["Inter", "Segoe UI", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Cascadia Code", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
