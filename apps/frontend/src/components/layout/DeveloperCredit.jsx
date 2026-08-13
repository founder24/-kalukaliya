export function DeveloperCredit({ className = '' }) {
  return (
    <p className={`text-xs text-muted-foreground/60 ${className}`}>
      Developed by{' '}
      <a
        href="https://ayanbhaumik.in/"
        target="_blank"
        rel="noopener noreferrer"
        className="font-medium text-muted-foreground hover:text-foreground transition-colors"
      >
        Ayan Bhaumik
      </a>
    </p>
  );
}
