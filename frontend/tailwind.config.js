/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#fbf4ef',
          100: '#f4e2d6',
          200: '#e7c7b2',
          300: '#d5a184',
          400: '#c07858',
          500: '#a55238',
          600: '#8c3b2a',
          700: '#6f2d20',
          800: '#552318',
          900: '#3b1811',
        },
        ink: {
          900: '#241c16',
          700: '#4a3f36',
          500: '#6f6258',
        },
        paper: {
          50: '#fbf7f0',
          100: '#f3efe6',
          200: '#e7dccb',
        },
      },
      fontFamily: {
        sans: ['Avenir Next', 'PingFang SC', 'Hiragino Sans GB', 'Noto Sans SC', 'Segoe UI', 'sans-serif'],
        serif: ['Songti SC', 'STSong', 'Iowan Old Style', 'Palatino Linotype', 'Noto Serif SC', 'serif'],
      },
      boxShadow: {
        sheet: '0 18px 40px -28px rgba(36, 28, 22, 0.45)',
      },
    },
  },
  plugins: [],
}
