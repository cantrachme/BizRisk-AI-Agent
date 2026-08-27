import type { Metadata } from 'next';
import './globals.css';
import AuthProvider from '../components/AuthProvider';

export const metadata: Metadata = {
  title: 'BizRisk AI Agent - Investigation Dashboard',
  description: 'Evidence-based business risk investigations and intelligence reporting dashboard.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <link rel="icon" href="/favicon.ico" />
      </head>
      <body>
        <AuthProvider>
          <main style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
            {children}
          </main>
        </AuthProvider>
      </body>
    </html>
  );
}
