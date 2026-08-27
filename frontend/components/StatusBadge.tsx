import React from 'react';

interface StatusBadgeProps {
  status: string;
}

export default function StatusBadge({ status }: StatusBadgeProps) {
  const norm = status.toUpperCase();

  const getStyle = (): React.CSSProperties => {
    switch (norm) {
      case 'COMPLETED':
        return { background: 'rgba(16, 185, 129, 0.1)', border: '1px solid rgba(16, 185, 129, 0.2)', color: '#34d399' };
      case 'FAILED':
        return { background: 'rgba(239, 68, 68, 0.1)', border: '1px solid rgba(239, 68, 68, 0.2)', color: '#f87171' };
      case 'WAITING_FOR_USER':
        return { 
          background: 'rgba(245, 158, 11, 0.1)', 
          border: '1px solid rgba(245, 158, 11, 0.2)', 
          color: '#fbbf24',
          animation: 'pulse 1.5s infinite ease-in-out'
        };
      case 'CREATED':
      case 'PENDING':
        return { background: 'rgba(107, 114, 128, 0.1)', border: '1px solid rgba(107, 114, 128, 0.2)', color: '#9ca3af' };
      default:
        // Running / Research / Discovery / Planning states
        return { 
          background: 'rgba(59, 130, 246, 0.1)', 
          border: '1px solid rgba(59, 130, 246, 0.2)', 
          color: '#60a5fa',
        };
    }
  };

  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      padding: '4px 10px',
      borderRadius: '12px',
      fontSize: '12.5px',
      fontWeight: '600',
      letterSpacing: '0.3px',
      textTransform: 'uppercase',
      ...getStyle()
    }}>
      {norm.replace(/_/g, ' ')}
    </span>
  );
}
