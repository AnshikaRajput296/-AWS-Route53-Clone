import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Route 53 | AWS Console Clone",
  description: "A focused Route 53 hosted zone management experience",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
