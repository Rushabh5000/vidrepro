import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "VidRepro",
  description: "Video → bug reproduction steps",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav className="nav">
          <Link href="/" className="brand">🎬 VidRepro</Link>
          <Link href="/">Dashboard</Link>
          <Link href="/upload">Upload</Link>
        </nav>
        {children}
      </body>
    </html>
  );
}
