/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        terminal: {
          bg: "#05070b",
          panel: "#0b111c",
          panel2: "#101827",
          line: "#1d2a3d",
          cyan: "#18e0c8",
          green: "#23d18b",
          red: "#ff5c7a",
          amber: "#f6c958"
        }
      }
    }
  },
  plugins: []
};
