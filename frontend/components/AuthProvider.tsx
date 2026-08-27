'use client';

import React, { createContext, useContext, useEffect, useState } from 'react';

interface AuthContextType {
  token: string | null;
  login: (userId: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType>({
  token: null,
  login: () => {},
  logout: () => {},
});

export const useAuth = () => useContext(AuthContext);

export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('bizrisk_token');
    }
    return null;
  });
  const [mounted, setMounted] = useState(false);
  const [userIdInput, setUserIdInput] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    setMounted(true);

    // Global listener for unauthorized logout events from API client
    const handleLogout = () => {
      setToken(null);
      localStorage.removeItem('bizrisk_token');
      setError('Session expired or unauthorized. Please log in again.');
    };

    window.addEventListener('auth_logout', handleLogout);
    return () => window.removeEventListener('auth_logout', handleLogout);
  }, []);

  const login = (userId: string) => {
    const trimmed = userId.trim();
    if (!trimmed) {
      setError('User ID cannot be empty.');
      return;
    }
    localStorage.setItem('bizrisk_token', trimmed);
    setToken(trimmed);
    setError('');
  };

  const logout = () => {
    localStorage.removeItem('bizrisk_token');
    setToken(null);
  };

  const handleLoginSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    login(userIdInput);
  };

  if (!mounted) {
    return (
      <div style={{ display: 'flex', flex: 1, alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
        <div className="spinner" />
      </div>
    );
  }

  if (!token) {
    return (
      <div style={containerStyle}>
        <div className="glass-panel" style={cardStyle}>
          <div style={headerStyle}>
            <div style={logoIconStyle}>🔍</div>
            <h1 style={titleStyle}>BizRisk AI</h1>
            <p style={subtitleStyle}>Advanced Agentic Risk Investigation Portal</p>
          </div>
          
          {error && <div style={errorStyle}>{error}</div>}

          <form onSubmit={handleLoginSubmit} style={formStyle}>
            <div style={inputGroupStyle}>
              <label style={labelStyle} htmlFor="user-id">User Authorization Identity</label>
              <input
                id="user-id"
                type="text"
                placeholder="e.g. UserA, UserB"
                value={userIdInput}
                onChange={(e) => setUserIdInput(e.target.value)}
                style={inputStyle}
                autoFocus
              />
            </div>
            
            <button type="submit" style={buttonStyle}>
              Access Dashboard
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ token, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

// Inline Styles for Login Form
const containerStyle: React.CSSProperties = {
  display: 'flex',
  flex: 1,
  alignItems: 'center',
  justifyContent: 'center',
  minHeight: '100vh',
  padding: '20px',
};

const cardStyle: React.CSSProperties = {
  width: '100%',
  maxWidth: '440px',
  padding: '40px',
  display: 'flex',
  flexDirection: 'column',
  gap: '24px',
};

const headerStyle: React.CSSProperties = {
  textAlign: 'center',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: '8px',
};

const logoIconStyle: React.CSSProperties = {
  fontSize: '48px',
  marginBottom: '8px',
};

const titleStyle: React.CSSProperties = {
  fontSize: '28px',
  fontWeight: '800',
  color: '#fff',
  letterSpacing: '-0.5px',
};

const subtitleStyle: React.CSSProperties = {
  fontSize: '14px',
  color: 'var(--foreground-muted)',
};

const formStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '20px',
};

const inputGroupStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '8px',
};

const labelStyle: React.CSSProperties = {
  fontSize: '13px',
  fontWeight: '600',
  color: 'var(--foreground-muted)',
};

const inputStyle: React.CSSProperties = {
  width: '100%',
};

const buttonStyle: React.CSSProperties = {
  width: '100%',
  padding: '14px',
};

const errorStyle: React.CSSProperties = {
  background: 'rgba(239, 68, 68, 0.1)',
  border: '1px solid rgba(239, 68, 68, 0.2)',
  color: '#f87171',
  padding: '12px',
  borderRadius: '8px',
  fontSize: '13.5px',
  textAlign: 'center',
};
