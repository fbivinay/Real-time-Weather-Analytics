import { Geist, Geist_Mono } from "next/font/google";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

import Shell from "../components/Shell";

const sans = Geist({ subsets: ["latin"], variable: "--font-sans" });
const mono = Geist_Mono({ subsets: ["latin"], variable: "--font-mono" });

const title = "WeatherOps — Weather-aware delivery intelligence";
const description =
  "Monitor, analyze and predict how rainfall affects ShopFlow India's delivery network: live operations, impact map, future order risk and history.";

export const metadata = {
  metadataBase: new URL("https://weather-ops-delivery-intelligence.vercel.app"),
  title,
  description,
  openGraph: {
    type: "website",
    url: "/",
    title,
    description,
    images: [{ url: "/og.png", width: 1440, height: 756 }],
  },
  twitter: {
    card: "summary_large_image",
    title,
    description,
    images: ["/og.png"],
  },
};

// Desktop only: phones and tablets get a message instead of the dashboard. Runs in
// <head>, before anything paints, so the dashboard never flashes on a phone.
const DESKTOP_ONLY_CSS = `
html[data-desktop-only]{display:flex!important;flex-direction:column;align-items:center;justify-content:center;height:100%;margin:0;padding:32px;box-sizing:border-box;overflow:hidden!important;background:#0f172a!important;color:#f1f5f9;font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;text-align:center}
html[data-desktop-only] body{display:none!important}
html[data-desktop-only]::before{content:"Please open this website on a laptop or desktop";max-width:340px;font-size:22px;font-weight:700;line-height:1.3}
html[data-desktop-only]::after{content:"It is built for larger screens and is not available on phones or tablets.";max-width:340px;margin-top:12px;font-size:15px;color:#94a3b8}`;
const DESKTOP_ONLY_JS = `(function(){try{var n=navigator,ua=n.userAgent||"",mq=function(q){return!!(window.matchMedia&&matchMedia(q).matches)};
if((n.userAgentData&&n.userAgentData.mobile)||/Android|iPhone|iPad|iPod|Mobile|Tablet|Silk|Kindle|PlayBook|BlackBerry|BB10|Opera Mini|IEMobile|webOS/i.test(ua)||(/Macintosh/.test(ua)&&n.maxTouchPoints>1)||(mq("(pointer: coarse)")&&!mq("(any-pointer: fine)"))||(navigator.maxTouchPoints>0&&(Math.min(screen.width,screen.height)<600||(/Linux (arm|aarch)|Android/i.test(navigator.platform)&&!/CrOS/.test(navigator.userAgent)))))document.documentElement.setAttribute("data-desktop-only","")}catch(e){}})();`;

export default function RootLayout({ children }) {
  return (
    // suppressHydrationWarning: the inline script sets data-desktop-only before React hydrates.
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <style dangerouslySetInnerHTML={{ __html: DESKTOP_ONLY_CSS }} />
        <script dangerouslySetInnerHTML={{ __html: DESKTOP_ONLY_JS }} />
      </head>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
