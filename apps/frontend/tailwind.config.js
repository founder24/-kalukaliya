/** @type {import('tailwindcss').Config} */
module.exports = {

  content: [
    "./src/**/*.{js,jsx,ts,tsx}",
    "./public/index.html",
  ],
  theme: {
    extend: {
      borderRadius: {
        sm: "calc(var(--radius) - 4px)",
        md: "calc(var(--radius) - 2px)",
        lg: "var(--radius)",
        xl: "calc(var(--radius) + 4px)",
        "2xl": "calc(var(--radius) + 8px)",
        "3xl": "calc(var(--radius) + 16px)",
      },
      colors: {
        background:  "hsl(var(--background))",
        foreground:  "hsl(var(--foreground))",
        card: {
          DEFAULT:    "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        popover: {
          DEFAULT:    "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        primary: {
          DEFAULT:    "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT:    "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT:    "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT:    "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        destructive: {
          DEFAULT:    "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        border: "hsl(var(--border))",
        input:  "hsl(var(--input))",
        ring:   "hsl(var(--ring))",
        /* ── Sidebar tokens ── */
        sidebar: {
          DEFAULT:            "hsl(var(--sidebar))",
          foreground:         "hsl(var(--sidebar-foreground))",
          primary:            "hsl(var(--sidebar-primary))",
          "primary-foreground":"hsl(var(--sidebar-primary-foreground))",
          accent:             "hsl(var(--sidebar-accent))",
          "accent-foreground":"hsl(var(--sidebar-accent-foreground))",
          border:             "hsl(var(--sidebar-border))",
          ring:               "hsl(var(--sidebar-ring))",
        },
        /* ── Charts ── */
        chart: {
          "1": "hsl(var(--chart-1))",
          "2": "hsl(var(--chart-2))",
          "3": "hsl(var(--chart-3))",
          "4": "hsl(var(--chart-4))",
          "5": "hsl(var(--chart-5))",
        },
      },
      fontFamily: {
        sans: ["'Space Grotesk'", "Inter", "-apple-system", "BlinkMacSystemFont", "'Segoe UI'", "sans-serif"],
      },
      keyframes: {
        /* Shadcn accordion */
        "accordion-down": {
          from: { height: "0" },
          to:   { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to:   { height: "0" },
        },
        /* Design-system animations */
        shimmer: {
          "0%":   { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition:  "200% 0" },
        },
        pulseGlow: {
          "0%, 100%": { boxShadow: "0 0 15px var(--glow-primary), 0 0 30px transparent" },
          "50%":      { boxShadow: "0 0 25px var(--glow-primary), 0 0 50px var(--glow-secondary)" },
        },
        orbit: {
          from: { transform: "rotate(0deg)" },
          to:   { transform: "rotate(360deg)" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0px)" },
          "50%":      { transform: "translateY(-6px)" },
        },
        meshFloat: {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "33%":      { transform: "translate(30px, -20px) scale(1.05)" },
          "66%":      { transform: "translate(-20px, 15px) scale(0.95)" },
        },
        borderGlow: {
          "0%, 100%": { borderColor: "rgba(124,58,237,0.2)" },
          "50%":      { borderColor: "rgba(124,58,237,0.5)" },
        },
        blink: {
          "0%, 100%": { opacity: "1" },
          "50%":      { opacity: "0" },
        },
        fadeIn: {
          from: { opacity: "0" },
          to:   { opacity: "1" },
        },
        slideUp: {
          from: { opacity: "0", transform: "translateY(10px)" },
          to:   { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up":   "accordion-up 0.2s ease-out",
        "shimmer":        "shimmer 3s linear infinite",
        "pulse-glow":     "pulseGlow 8s ease-in-out infinite",
        "orbit":          "orbit 20s linear infinite",
        "float":          "float 10s ease-in-out infinite",
        "mesh-float":     "meshFloat 45s ease-in-out infinite",
        "border-glow":    "borderGlow 10s ease-in-out infinite",
        "blink":          "blink 0.65s ease-in-out infinite",
        "fade-in":        "fadeIn 0.4s ease-out",
        "slide-up":       "slideUp 0.3s ease-out",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
