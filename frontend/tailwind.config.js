/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      colors: {
        felt: '#1a6b3c',
        'felt-dark': '#124d2b',
        'felt-light': '#1e8449',
      },
    },
  },
  plugins: [],
}
