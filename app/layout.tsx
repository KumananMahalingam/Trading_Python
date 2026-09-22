import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Quanta — Automated trading workspace',
  description: 'A technical-analysis trading workspace for monitoring automated stock strategies.',
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>
}
