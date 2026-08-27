'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useAuth } from '../../components/AuthProvider';
import { api, APIError } from '../../lib/api';
import { InvestigationListItem } from '../../types';
import StatusBadge from '../../components/StatusBadge';

export default function Dashboard() {
  const { token, logout } = useAuth();
  const [history, setHistory] = useState<InvestigationListItem[]>([]);
  const [incomplete, setIncomplete] = useState<InvestigationListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchInvestigations = async () => {
    try {
      setLoading(true);
      setError('');
      const [histData, incData] = await Promise.all([
        api.getInvestigations(),
        api.getIncompleteInvestigations(),
      ]);
      
      // Sort by created timestamp descending
      const sortFn = (a: InvestigationListItem, b: InvestigationListItem) => {
        const ad = a.created_at ? new Date(a.created_at).getTime() : 0;
        const bd = b.created_at ? new Date(b.created_at).getTime() : 0;
        return bd - ad;
      };
      
      setHistory(histData.sort(sortFn));
      setIncomplete(incData.sort(sortFn));
    } catch (err: any) {
      if (err instanceof APIError) {
        setError(err.message);
      } else {
        setError('Failed to load investigations. Please check connection.');
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchInvestigations();
  }, []);

  const formatDate = (isoString: string | null) => {
    if (!isoString) return 'N/A';
    try {
      return new Date(isoString).toLocaleString();
    } catch {
      return isoString;
    }
  };

  return (
    <div style={containerStyle}>
      {/* Header bar */}
      <header className="glass-panel" style={headerStyle}>
        <div style={headerLeftStyle}>
          <span style={logoStyle}>🔍</span>
          <span style={titleStyle}>BizRisk Dashboard</span>
        </div>
        <div style={headerRightStyle}>
          <span style={userIndicatorStyle}>Identity: <strong>{token}</strong></span>
          <button onClick={logout} style={logoutButtonStyle}>Logout</button>
        </div>
      </header>

      {/* Main content grid */}
      <div style={mainContentStyle}>
        {/* Actions panel */}
        <div style={actionsPanelStyle}>
          <h2 style={sectionTitleStyle}>Workspace Actions</h2>
          <div style={actionButtonsContainerStyle}>
            <Link href="/investigate">
              <button style={createButtonStyle}>+ Start New Investigation</button>
            </Link>
            <button onClick={fetchInvestigations} disabled={loading} style={refreshButtonStyle}>
              {loading ? 'Refreshing...' : '🔄 Refresh List'}
            </button>
          </div>
        </div>

        {error && <div style={errorStyle}>{error}</div>}

        {loading ? (
          <div style={loadingContainerStyle}>
            <div className="spinner" />
            <p>Syncing workspace state...</p>
          </div>
        ) : (
          <div style={listsGridStyle}>
            {/* Active/Incomplete Panel */}
            <div className="glass-panel" style={listPanelStyle}>
              <h3 style={listHeaderStyle}>Active & Pending Tasks ({incomplete.length})</h3>
              {incomplete.length === 0 ? (
                <div style={emptyStateStyle}>No active investigations. All tasks complete.</div>
              ) : (
                <div style={tableContainerStyle}>
                  <table style={tableStyle}>
                    <thead>
                      <tr>
                        <th style={thStyle}>Investigation ID</th>
                        <th style={thStyle}>Last Node</th>
                        <th style={thStyle}>Status</th>
                        <th style={thStyle}>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {incomplete.map((inv) => (
                        <tr key={inv.id} style={trStyle}>
                          <td style={tdIdStyle}>
                            <Link href={`/investigations/${inv.id}`} style={linkStyle}>
                              {inv.id}
                            </Link>
                          </td>
                          <td style={tdStyle}>{inv.current_node || 'Intake'}</td>
                          <td style={tdStyle}><StatusBadge status={inv.status} /></td>
                          <td style={tdStyle}>
                            <Link href={`/investigations/${inv.id}`}>
                              <button style={viewButtonStyle}>Track</button>
                            </Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Complete History Panel */}
            <div className="glass-panel" style={listPanelStyle}>
              <h3 style={listHeaderStyle}>All Historical Cases ({history.length})</h3>
              {history.length === 0 ? (
                <div style={emptyStateStyle}>Workspace history is empty. Launch a case above.</div>
              ) : (
                <div style={tableContainerStyle}>
                  <table style={tableStyle}>
                    <thead>
                      <tr>
                        <th style={thStyle}>Investigation ID</th>
                        <th style={thStyle}>Created At</th>
                        <th style={thStyle}>Status</th>
                        <th style={thStyle}>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {history.map((inv) => (
                        <tr key={inv.id} style={trStyle}>
                          <td style={tdIdStyle}>
                            <Link href={`/investigations/${inv.id}`} style={linkStyle}>
                              {inv.id}
                            </Link>
                          </td>
                          <td style={tdStyle}>{formatDate(inv.created_at)}</td>
                          <td style={tdStyle}><StatusBadge status={inv.status} /></td>
                          <td style={tdStyle}>
                            <Link href={`/investigations/${inv.id}`}>
                              <button style={viewButtonStyle}>Open</button>
                            </Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// Styles
const containerStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  flex: 1,
  padding: '30px',
  gap: '30px',
  maxWidth: '1280px',
  margin: '0 auto',
  width: '100%',
};

const headerStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  padding: '20px 30px',
};

const headerLeftStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '12px',
};

const logoStyle: React.CSSProperties = {
  fontSize: '28px',
};

const titleStyle: React.CSSProperties = {
  fontSize: '22px',
  fontWeight: '800',
  letterSpacing: '-0.5px',
};

const headerRightStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '20px',
};

const userIndicatorStyle: React.CSSProperties = {
  fontSize: '14px',
  color: 'var(--foreground-muted)',
};

const logoutButtonStyle: React.CSSProperties = {
  background: 'rgba(239, 68, 68, 0.15)',
  border: '1px solid rgba(239, 68, 68, 0.25)',
  color: '#f87171',
  padding: '8px 16px',
  fontSize: '13.5px',
};

const mainContentStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '30px',
};

const actionsPanelStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  flexWrap: 'wrap',
  gap: '20px',
};

const sectionTitleStyle: React.CSSProperties = {
  fontSize: '18px',
  fontWeight: '700',
};

const actionButtonsContainerStyle: React.CSSProperties = {
  display: 'flex',
  gap: '12px',
};

const createButtonStyle: React.CSSProperties = {
  padding: '12px 24px',
};

const refreshButtonStyle: React.CSSProperties = {
  background: 'rgba(255, 255, 255, 0.05)',
  border: '1px solid rgba(255, 255, 255, 0.08)',
  color: '#fff',
};

const errorStyle: React.CSSProperties = {
  background: 'rgba(239, 68, 68, 0.1)',
  border: '1px solid rgba(239, 68, 68, 0.2)',
  color: '#f87171',
  padding: '16px',
  borderRadius: '8px',
};

const loadingContainerStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  padding: '80px',
  gap: '16px',
  color: 'var(--foreground-muted)',
};

const listsGridStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(500px, 1fr))',
  gap: '30px',
};

const listPanelStyle: React.CSSProperties = {
  padding: '24px',
  display: 'flex',
  flexDirection: 'column',
  gap: '16px',
};

const listHeaderStyle: React.CSSProperties = {
  fontSize: '16px',
  fontWeight: '700',
  borderBottom: '1px solid rgba(255, 255, 255, 0.05)',
  paddingBottom: '12px',
};

const emptyStateStyle: React.CSSProperties = {
  padding: '40px 20px',
  textAlign: 'center',
  color: 'var(--foreground-muted)',
  fontSize: '14.5px',
};

const tableContainerStyle: React.CSSProperties = {
  overflowX: 'auto',
};

const tableStyle: React.CSSProperties = {
  width: '100%',
  borderCollapse: 'collapse',
  textAlign: 'left',
};

const thStyle: React.CSSProperties = {
  fontSize: '12px',
  fontWeight: '700',
  color: 'var(--foreground-muted)',
  textTransform: 'uppercase',
  padding: '12px 16px',
  borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
};

const trStyle: React.CSSProperties = {
  borderBottom: '1px solid rgba(255, 255, 255, 0.04)',
};

const tdStyle: React.CSSProperties = {
  padding: '14px 16px',
  fontSize: '14px',
};

const tdIdStyle: React.CSSProperties = {
  ...tdStyle,
  fontFamily: 'monospace',
  fontWeight: '600',
};

const linkStyle: React.CSSProperties = {
  color: 'var(--primary)',
};

const viewButtonStyle: React.CSSProperties = {
  padding: '6px 12px',
  fontSize: '12.5px',
  background: 'rgba(255, 255, 255, 0.05)',
  border: '1px solid rgba(255, 255, 255, 0.08)',
  borderRadius: '6px',
};
