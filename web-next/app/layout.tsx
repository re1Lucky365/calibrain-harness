import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans_Condensed } from "next/font/google";
import "./globals.css";

const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-plex-mono" });
const cond = IBM_Plex_Sans_Condensed({ subsets: ["latin"], weight: ["500", "600", "700"], variable: "--font-plex-cond" });

export const metadata: Metadata = {
  title: "Classifier Trading Terminal",
  description: "Kairos-driven perpetuals on Bybit testnet: live market data, decisions, execution and evaluation.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#000000" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${mono.variable} ${cond.variable}`}>
      <body>{children}</body>
    </html>
  );
}
