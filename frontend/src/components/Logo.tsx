export default function Logo({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="7" fill="#1f3a5f" />
      <path d="M9 22V10h6.2c4 0 6.8 2.4 6.8 6s-2.8 6-6.8 6H9zm3.2-2.8h2.9c2.2 0 3.7-1.3 3.7-3.2s-1.5-3.2-3.7-3.2h-2.9v6.4z" fill="#fff" />
      <circle cx="24.5" cy="10" r="2.2" fill="#4fb3a0" />
    </svg>
  );
}
