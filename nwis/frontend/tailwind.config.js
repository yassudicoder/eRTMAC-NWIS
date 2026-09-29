/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        oil: {
          50: '#eef4ff', 100: '#dae6ff', 200: '#bdd3ff', 300: '#90b5ff',
          400: '#5b8cfc', 500: '#3566f4', 600: '#2047e0',
          700: '#1a37b5', 800: '#1b318f', 900: '#1c2f72', 950: '#141e47',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
}
