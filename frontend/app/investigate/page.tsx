'use client';

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { api, APIError } from '../../lib/api';

export default function Investigate() {
  const router = useRouter();
  const [formData, setFormData] = useState({
    business_name: '',
    gstin: '',
    cin: '',
    website: '',
    location: '',
    additional_information: '',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    const { business_name, gstin, cin, website } = formData;
    if (!business_name.trim() && !gstin.trim() && !cin.trim() && !website.trim()) {
      setError('Please provide at least one key identifier: Company Name, GSTIN, CIN, or Website.');
      return;
    }

    try {
      setLoading(true);
      const payload: Record<string, string> = {};
      
      // Only attach non-empty fields
      Object.entries(formData).forEach(([key, val]) => {
        if (val.trim()) {
          payload[key] = val.trim();
        }
      });

      const res = await api.createInvestigation(payload);
      router.push(`/investigations/${res.id}`);
    } catch (err: any) {
      if (err instanceof APIError) {
        setError(err.message);
      } else {
        setError('Failed to initiate investigation. Please verify connection.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={containerStyle}>
      {/* Navigation breadcrumb */}
      <div style={breadcrumbStyle}>
        <Link href="/dashboard" style={backLinkStyle}>
          ← Back to Dashboard
        </Link>
      </div>

      <div className="glass-panel" style={formCardStyle}>
        <div style={headerStyle}>
          <span style={iconStyle}>🛡️</span>
          <h1 style={titleStyle}>Launch Investigation</h1>
          <p style={subtitleStyle}>Provide known identifiers to trigger the AI Agent graph workflow.</p>
        </div>

        {error && <div style={errorStyle}>{error}</div>}

        <form onSubmit={handleSubmit} style={formStyle}>
          {/* Business Name */}
          <div style={inputGroupStyle}>
            <label style={labelStyle} htmlFor="business_name">Company / Business Name</label>
            <input
              id="business_name"
              name="business_name"
              type="text"
              placeholder="e.g. ABC Foods Private Limited"
              value={formData.business_name}
              onChange={handleChange}
            />
          </div>

          <div style={rowStyle}>
            {/* GSTIN */}
            <div style={colStyle}>
              <label style={labelStyle} htmlFor="gstin">GSTIN (15 Digits)</label>
              <input
                id="gstin"
                name="gstin"
                type="text"
                placeholder="e.g. 27ABCDE1234F1Z5"
                value={formData.gstin}
                onChange={handleChange}
              />
            </div>
            {/* CIN */}
            <div style={colStyle}>
              <label style={labelStyle} htmlFor="cin">CIN (21 Digits)</label>
              <input
                id="cin"
                name="cin"
                type="text"
                placeholder="e.g. L12345MH2020PLC000001"
                value={formData.cin}
                onChange={handleChange}
              />
            </div>
          </div>

          <div style={rowStyle}>
            {/* Website */}
            <div style={colStyle}>
              <label style={labelStyle} htmlFor="website">Company Website URL</label>
              <input
                id="website"
                name="website"
                type="text"
                placeholder="e.g. abcfoods.in"
                value={formData.website}
                onChange={handleChange}
              />
            </div>
            {/* Location */}
            <div style={colStyle}>
              <label style={labelStyle} htmlFor="location">Operating State / Location</label>
              <input
                id="location"
                name="location"
                type="text"
                placeholder="e.g. Noida, Uttar Pradesh"
                value={formData.location}
                onChange={handleChange}
              />
            </div>
          </div>

          {/* Additional Info */}
          <div style={inputGroupStyle}>
            <label style={labelStyle} htmlFor="additional_information">Additional Corporate Context</label>
            <textarea
              id="additional_information"
              name="additional_information"
              rows={3}
              placeholder="Provide any extra details like registered addresses, keywords, or background information..."
              value={formData.additional_information}
              onChange={handleChange}
              style={textareaStyle}
            />
          </div>

          <div style={buttonContainerStyle}>
            <Link href="/dashboard" style={{ flex: 1 }}>
              <button type="button" style={cancelButtonStyle}>Cancel</button>
            </Link>
            <button type="submit" disabled={loading} style={submitButtonStyle}>
              {loading ? 'Initializing Agent Workflow...' : 'Start Investigation Graph'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// Styles
const containerStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  flex: 1,
  padding: '40px 20px',
  gap: '24px',
  maxWidth: '680px',
  margin: '0 auto',
  width: '100%',
};

const breadcrumbStyle: React.CSSProperties = {
  display: 'flex',
};

const backLinkStyle: React.CSSProperties = {
  color: 'var(--foreground-muted)',
  fontSize: '14.5px',
  fontWeight: '500',
  transition: 'color 0.2s',
};

const formCardStyle: React.CSSProperties = {
  padding: '40px',
  display: 'flex',
  flexDirection: 'column',
  gap: '30px',
};

const headerStyle: React.CSSProperties = {
  textAlign: 'center',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: '8px',
};

const iconStyle: React.CSSProperties = {
  fontSize: '44px',
  marginBottom: '8px',
};

const titleStyle: React.CSSProperties = {
  fontSize: '24px',
  fontWeight: '800',
  color: '#fff',
  letterSpacing: '-0.5px',
};

const subtitleStyle: React.CSSProperties = {
  fontSize: '14.5px',
  color: 'var(--foreground-muted)',
  maxWidth: '480px',
};

const formStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '24px',
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

const rowStyle: React.CSSProperties = {
  display: 'flex',
  gap: '20px',
  flexWrap: 'wrap',
};

const colStyle: React.CSSProperties = {
  flex: 1,
  minWidth: '240px',
  display: 'flex',
  flexDirection: 'column',
  gap: '8px',
};

const textareaStyle: React.CSSProperties = {
  fontFamily: 'inherit',
  resize: 'vertical',
};

const errorStyle: React.CSSProperties = {
  background: 'rgba(239, 68, 68, 0.1)',
  border: '1px solid rgba(239, 68, 68, 0.2)',
  color: '#f87171',
  padding: '16px',
  borderRadius: '8px',
  fontSize: '14px',
};

const buttonContainerStyle: React.CSSProperties = {
  display: 'flex',
  gap: '16px',
  marginTop: '12px',
};

const cancelButtonStyle: React.CSSProperties = {
  background: 'rgba(255, 255, 255, 0.05)',
  border: '1px solid rgba(255, 255, 255, 0.08)',
  color: '#fff',
  width: '100%',
};

const submitButtonStyle: React.CSSProperties = {
  flex: 2,
};
