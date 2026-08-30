'use client';

import React, { useEffect, useState, useRef } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { api, APIError } from '../../../lib/api';
import { 
  InvestigationDetail, 
  EvidenceItem, 
  RiskAnalysis, 
  HistoricalReport, 
  HumanInterventionStatus 
} from '../../../types';
import StatusBadge from '../../../components/StatusBadge';

interface CandidateItem {
  name?: string;
  gstin?: string;
  cin?: string;
  location?: string;
  confidence?: number;
}

interface FindingItem {
  code?: string;
  confidence?: number;
  description?: string;
  evidence_ids?: string[];
}

export default function InvestigationPage() {
  const params = useParams();
  const id = params.id as string;

  const [detail, setDetail] = useState<InvestigationDetail | null>(null);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [risk, setRisk] = useState<RiskAnalysis | null>(null);
  const [reports, setReports] = useState<HistoricalReport[]>([]);
  const [selectedReportIdx, setSelectedReportIdx] = useState<number>(0);
  const [hitl, setHitl] = useState<HumanInterventionStatus | null>(null);
  
  const [loading, setLoading] = useState(true);
  const [polling, setPolling] = useState(false);
  const [resumeLoading, setResumeLoading] = useState(false);
  const [error, setError] = useState('');
  const [pipelineCollapsed, setPipelineCollapsed] = useState(true);
  
  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);

  // 1. Single Fetch function for all investigation data
  const fetchData = async (isPoll = false) => {
    try {
      if (!isPoll) setLoading(true);
      setError('');
      
      const detailData = await api.getInvestigation(id);
      setDetail(detailData);

      // Fetch supplementary components in parallel
      const [evData, reportsData] = await Promise.all([
        api.getEvidence(id).catch(() => [] as EvidenceItem[]),
        api.getReports(id).catch(() => [] as HistoricalReport[]),
      ]);

      setEvidence(evData);
      
      if (reportsData.length > 0) {
        setReports(reportsData);
        // By default, select the latest version
        setSelectedReportIdx(reportsData.length - 1);
      }

      // Fetch risk and HITL based on current status
      if (detailData.status === 'WAITING_FOR_USER') {
        const hitlData = await api.getHumanIntervention(id).catch(() => null);
        setHitl(hitlData);
      } else {
        setHitl(null);
      }

      if (detailData.status === 'COMPLETED' || detailData.risk_score !== null) {
        const riskData = await api.getRisk(id).catch(() => null);
        setRisk(riskData);
      }

      // Check if polling is required
      const terminalStates = ['COMPLETED', 'FAILED'];
      const shouldPoll = !terminalStates.includes(detailData.status.toUpperCase());
      setPolling(shouldPoll);

    } catch (err) {
      if (err instanceof APIError) {
        setError(err.message);
      } else {
        setError('Failed to refresh investigation details.');
      }
    } finally {
      if (!isPoll) setLoading(false);
    }
  };

  // 2. Control Polling Loop
  useEffect(() => {
    fetchData();

    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, [id]);

  useEffect(() => {
    if (polling) {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
      
      pollTimerRef.current = setInterval(() => {
        fetchData(true);
      }, 4000);
    } else {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    }

    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, [polling]);

  // 3. Trigger Resume action
  const handleResume = async () => {
    try {
      setResumeLoading(true);
      setError('');
      await api.resumeInvestigation(id);
      setHitl(null);
      await fetchData(); // Force immediate reload
    } catch (err) {
      if (err instanceof APIError) {
        setError(err.message);
      } else {
        setError('Failed to resume investigation.');
      }
    } finally {
      setResumeLoading(false);
    }
  };

  if (loading) {
    return (
      <div style={loadingContainerStyle}>
        <div className="spinner" />
        <p style={{ marginTop: '16px' }}>Fetching investigation timeline...</p>
      </div>
    );
  }

  if (error && !detail) {
    return (
      <div style={containerStyle}>
        <div style={breadcrumbStyle}>
          <Link href="/dashboard" style={backLinkStyle}>← Back to Dashboard</Link>
        </div>
        <div style={errorStyle}>{error}</div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div style={containerStyle}>
        <div style={breadcrumbStyle}>
          <Link href="/dashboard" style={backLinkStyle}>← Back to Dashboard</Link>
        </div>
        <div style={errorStyle}>Investigation case not found.</div>
      </div>
    );
  }

  // Parse discovered candidates from evidence
  const candidateEvidence = evidence.find(ev => ev.field_name === 'candidate_entities');
  let candidates: CandidateItem[] = [];
  if (candidateEvidence) {
    try {
      candidates = JSON.parse(candidateEvidence.field_value) as CandidateItem[];
    } catch {
      candidates = [];
    }
  }

  const isOfficialSource = (sourceName: string) => {
    const name = sourceName.toLowerCase();
    return name.includes('gst') || name.includes('mca') || name.includes('registry');
  };

  const getRiskColor = (level: string | null) => {
    if (!level) return 'var(--risk-unknown)';
    switch (level.toUpperCase()) {
      case 'LOW': return 'var(--risk-low)';
      case 'MODERATE': return 'var(--risk-moderate)';
      case 'HIGH': return 'var(--risk-high)';
      case 'VERY_HIGH': return 'var(--risk-very-high)';
      default: return 'var(--risk-unknown)';
    }
  };

  const getRiskGlow = (level: string | null) => {
    if (!level) return 'transparent';
    switch (level.toUpperCase()) {
      case 'LOW': return 'var(--risk-low-glow)';
      case 'MODERATE': return 'var(--risk-moderate-glow)';
      case 'HIGH': return 'var(--risk-high-glow)';
      case 'VERY_HIGH': return 'var(--risk-very-high-glow)';
      default: return 'transparent';
    }
  };

  const getStageState = (stage: string) => {
    const status = detail.status.toUpperCase();
    const node = (detail.current_node || '').toUpperCase();
    
    // Intake
    if (stage === 'Intake') {
      return { isCompleted: true, isActive: false };
    }
    
    // Entity Discovery
    if (stage === 'Entity Discovery') {
      const isCompleted = ['PENDING_RESEARCH', 'RESEARCH', 'ENTITY_RESOLUTION', 'RISK_ANALYSIS', 'REPORT_GENERATION', 'QA', 'COMPLETED', 'FAILED'].includes(status) || node !== 'INTAKE';
      const isActive = status === 'NORMALIZED' || node === 'DISCOVERY';
      return { isCompleted, isActive };
    }
    
    // Browser Research
    if (stage === 'Browser Research') {
      const isCompleted = ['ENTITY_RESOLUTION', 'RISK_ANALYSIS', 'REPORT_GENERATION', 'QA', 'COMPLETED', 'FAILED'].includes(status);
      const isActive = ['PENDING_RESEARCH', 'RESEARCH'].includes(status) || node === 'BROWSER_RESEARCH';
      return { isCompleted, isActive };
    }
    
    // Evidence Validation
    if (stage === 'Evidence Validation') {
      const isCompleted = ['ENTITY_RESOLUTION', 'RISK_ANALYSIS', 'REPORT_GENERATION', 'QA', 'COMPLETED', 'FAILED'].includes(status);
      const isActive = ['PENDING_RESEARCH', 'RESEARCH'].includes(status) || node === 'BROWSER_RESEARCH';
      return { isCompleted, isActive };
    }
    
    // Entity Resolution
    if (stage === 'Entity Resolution') {
      const isCompleted = ['RISK_ANALYSIS', 'REPORT_GENERATION', 'QA', 'COMPLETED', 'FAILED'].includes(status);
      const isActive = status === 'ENTITY_RESOLUTION' || node === 'ENTITY_RESOLUTION';
      return { isCompleted, isActive };
    }
    
    // Risk Analysis
    if (stage === 'Risk Analysis') {
      const isCompleted = ['REPORT_GENERATION', 'QA', 'COMPLETED', 'FAILED'].includes(status);
      const isActive = status === 'RISK_ANALYSIS' || node === 'RISK_ANALYSIS';
      return { isCompleted, isActive };
    }
    
    // Report
    if (stage === 'Report') {
      const isCompleted = ['QA', 'COMPLETED', 'FAILED'].includes(status);
      const isActive = status === 'REPORT_GENERATION' || node === 'REPORT_GENERATION';
      return { isCompleted, isActive };
    }
    
    // QA
    if (stage === 'QA') {
      const isCompleted = ['COMPLETED', 'FAILED'].includes(status);
      const isActive = status === 'QA' || node === 'QA';
      return { isCompleted, isActive };
    }
    
    return { isCompleted: false, isActive: false };
  };

  const browserSessions = detail?.browser_sessions || [];
  
  // Calculate browser statistics
  const totalAttempted = browserSessions.length;
  const successfulCount = browserSessions.filter((s: any) => s.status === 'SUCCESS').length;
  const blockedCount = browserSessions.filter((s: any) => s.status === 'BLOCKED' || s.status === 'BLOCKED_OR_ERROR').length;
  const irrelevantCount = browserSessions.filter((s: any) => s.status === 'IRRELEVANT_CONTENT' || s.status === 'IRRELEVANT').length;
  const failedCount = totalAttempted - successfulCount - blockedCount - irrelevantCount;
  
  const fallbackUsed = browserSessions.some((s: any) => s.source_type === 'fallback') ? 'YES' : 'NO';
  const selectedSourceObj = browserSessions.find((s: any) => s.selected_as_evidence === true);
  const selectedSource = selectedSourceObj ? selectedSourceObj.source_name : 'None';

  return (
    <div style={containerStyle}>
      {/* Top Breadcrumb & Controls */}
      <div style={topControlsStyle}>
        <Link href="/dashboard" style={backLinkStyle}>← Back to Dashboard</Link>
        <div style={pollingIndicatorContainerStyle}>
          {polling && (
            <div style={pulseIndicatorStyle}>
              <span className="skeleton" style={pulseDotStyle} />
              <span style={{ fontSize: '13px', color: 'var(--foreground-muted)' }}>Auto-sync active...</span>
            </div>
          )}
          <button onClick={() => fetchData()} style={manualRefreshButtonStyle}>
            🔄 Sync Now
          </button>
        </div>
      </div>

      {/* Case Header */}
      <div className="glass-panel" style={caseHeaderStyle}>
        <div style={headerLeftSectionStyle}>
          <span style={headerLabelStyle}>Case Investigation File</span>
          <h1 style={caseTitleStyle}>{detail.input.business_name || 'Unnamed Business'}</h1>
          <p style={caseIdStyle}>UUID: <code>{detail.id}</code></p>
        </div>
        <div style={headerRightSectionStyle}>
          <div style={headerMetricStyle}>
            <span style={metricLabelStyle}>Status</span>
            <StatusBadge status={detail.status} />
          </div>
          <div style={headerMetricStyle}>
            <span style={metricLabelStyle}>Current Abstraction</span>
            <span style={metricValueStyle}>{detail.current_node || 'Intake / Created'}</span>
          </div>
        </div>
      </div>

      {/* HITL HUMAN INTERVENTION PANEL */}
      {detail.status === 'WAITING_FOR_USER' && hitl && (
        <div className="glass-panel" style={hitlPanelStyle}>
          <div style={hitlHeaderStyle}>
            <span style={hitlIconStyle}>⚠️</span>
            <div>
              <h3 style={hitlTitleStyle}>Action Required: Human Intervention Triggered</h3>
              <p style={hitlDescStyle}>The automated browser crawler encountered a restriction and is paused waiting for bypass.</p>
            </div>
          </div>
          <div style={hitlBodyStyle}>
            {hitl.pending_tasks.map((task) => (
              <div key={task.id} style={hitlTaskCardStyle}>
                <div style={hitlTaskMetaStyle}>
                  <span>Task: <strong>{task.task_type}</strong></span>
                  <span>Type: <strong style={{ color: '#f59e0b' }}>{task.intervention_type}</strong></span>
                </div>
                <p style={hitlTaskReasonStyle}><strong>Reason:</strong> {task.intervention_reason || 'Manual verification page loaded.'}</p>
                <p style={hitlTaskReasonStyle}><strong>Objective:</strong> {task.objective}</p>
              </div>
            ))}
          </div>
          <div style={hitlActionsContainerStyle}>
            <button onClick={handleResume} disabled={resumeLoading} style={resumeButtonStyle}>
              {resumeLoading ? 'Initiating pipeline recovery...' : 'Resume Investigation Graph'}
            </button>
          </div>
        </div>
      )}

      {error && <div style={errorStyle}>{error}</div>}

      {/* FAILED INVESTIGATION PANEL */}
      {detail.status === 'FAILED' && (
        <div className="glass-panel" style={{
          padding: '20px',
          border: '1px solid rgba(239, 68, 68, 0.3)',
          borderRadius: '12px',
          background: 'rgba(239, 68, 68, 0.02)',
          display: 'flex',
          gap: '16px',
          alignItems: 'center',
          boxShadow: '0 0 16px rgba(239, 68, 68, 0.05)',
        }}>
          <span style={{ fontSize: '24px' }}>❌</span>
          <div>
            <h3 style={{ margin: 0, fontWeight: '700', color: '#f87171' }}>Investigation Case Failed</h3>
            <p style={{ margin: '4px 0 0 0', fontSize: '14px', color: 'var(--foreground-muted)' }}>
              The generic web research or QA validation thresholds failed to resolve enough verified entity markers. This case is terminated.
            </p>
          </div>
        </div>
      )}

      <div style={twoColumnLayoutGridStyle}>
        {/* Left Hand: Investigation Specs & Evidence */}
        <div style={leftColStyle}>
          
          {/* Input Details */}
          <div className="glass-panel" style={innerPanelStyle}>
            <h3 style={panelHeaderStyle}>Intake Specifications</h3>
            <div style={specGridStyle}>
              <div style={specItemStyle}>
                <span style={specLabelStyle}>GSTIN</span>
                <span style={specValueStyle}>{detail.input.gstin || 'None'}</span>
              </div>
              <div style={specItemStyle}>
                <span style={specLabelStyle}>CIN</span>
                <span style={specValueStyle}>{detail.input.cin || 'None'}</span>
              </div>
              <div style={specItemStyle}>
                <span style={specLabelStyle}>Website</span>
                <span style={specValueStyle}>{detail.input.website || 'None'}</span>
              </div>
              <div style={specItemStyle}>
                <span style={specLabelStyle}>Location</span>
                <span style={specValueStyle}>{detail.input.location || 'None'}</span>
              </div>
            </div>
          </div>

          {/* Research Pipeline Progress and Browser Attempts */}
          <div className="glass-panel" style={innerPanelStyle}>
            <h3 style={panelHeaderStyle}>Research Pipeline Logs</h3>
            
            {/* Visual stage progression indicator */}
            <div style={{
              display: 'flex',
              gap: '6px',
              justifyContent: 'space-between',
              background: 'rgba(255,255,255,0.01)',
              border: '1px solid rgba(255,255,255,0.04)',
              borderRadius: '8px',
              padding: '12px',
              overflowX: 'auto',
            }}>
              {['Intake', 'Entity Discovery', 'Browser Research', 'Evidence Validation', 'Entity Resolution', 'Risk Analysis', 'Report', 'QA'].map((stage, idx, arr) => {
                const { isCompleted, isActive } = getStageState(stage);
                return (
                  <React.Fragment key={stage}>
                    <div style={{
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      minWidth: '70px',
                      textAlign: 'center',
                      opacity: isActive || isCompleted ? 1 : 0.3,
                    }}>
                      <span style={{
                        width: '24px',
                        height: '24px',
                        borderRadius: '50%',
                        background: isCompleted ? '#10b981' : isActive ? '#f59e0b' : 'rgba(255,255,255,0.1)',
                        color: isCompleted || isActive ? '#000' : '#fff',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '11px',
                        fontWeight: 'bold',
                        marginBottom: '4px',
                      }}>
                        {isCompleted ? '✓' : idx + 1}
                      </span>
                      <span style={{
                        fontSize: '10px',
                        color: isCompleted ? '#10b981' : isActive ? '#f59e0b' : '#9ca3af',
                        fontWeight: '600',
                      }}>{stage}</span>
                    </div>
                    {idx < arr.length - 1 && (
                      <span style={{
                        alignSelf: 'center',
                        fontSize: '12px',
                        color: 'rgba(255,255,255,0.15)',
                        marginBottom: '16px',
                      }}>→</span>
                    )}
                  </React.Fragment>
                );
              })}
            </div>

            {/* Browser Research statistics summary */}
            <div style={{
              marginTop: '16px',
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.05)',
              borderRadius: '8px',
              padding: '12px',
            }}>
              <h4 style={{ margin: '0 0 8px 0', fontSize: '13px', color: '#fff', fontWeight: 'bold' }}>Browser Agent Session Stats</h4>
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))',
                gap: '8px',
                fontSize: '12px',
              }}>
                <div>Attempted: <strong>{totalAttempted}</strong></div>
                <div>Successful: <strong style={{ color: '#34d399' }}>{successfulCount}</strong></div>
                <div>Blocked: <strong style={{ color: '#f87171' }}>{blockedCount}</strong></div>
                <div>Irrelevant: <strong style={{ color: '#f59e0b' }}>{irrelevantCount}</strong></div>
                <div>Failed: <strong style={{ color: '#ef4444' }}>{failedCount}</strong></div>
                <div>Fallback Used: <strong>{fallbackUsed}</strong></div>
                <div style={{ gridColumn: '1 / -1' }}>Final Selected Source: <strong style={{ color: '#60a5fa' }}>{selectedSource}</strong></div>
              </div>
            </div>

            {/* Collapsible Source Attempts log */}
            {totalAttempted > 0 && (
              <div style={{ marginTop: '12px' }}>
                <button 
                  onClick={() => setPipelineCollapsed(!pipelineCollapsed)}
                  style={{
                    width: '100%',
                    background: 'rgba(255,255,255,0.03)',
                    border: '1px solid rgba(255,255,255,0.06)',
                    borderRadius: '6px',
                    padding: '8px',
                    color: '#fff',
                    fontSize: '12px',
                    fontWeight: '600',
                    cursor: 'pointer',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}
                >
                  <span>{pipelineCollapsed ? '▶ Show Detailed Source Attempts Log' : '▼ Hide Detailed Source Attempts Log'}</span>
                  <span>({totalAttempted} Attempts)</span>
                </button>

                {!pipelineCollapsed && (
                  <div style={{
                    maxHeight: '300px',
                    overflowY: 'auto',
                    border: '1px solid rgba(255,255,255,0.06)',
                    borderRadius: '6px',
                    marginTop: '8px',
                    background: 'rgba(0,0,0,0.2)',
                  }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11px', textAlign: 'left' }}>
                      <thead>
                        <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.1)', background: 'rgba(255,255,255,0.02)' }}>
                          <th style={{ padding: '6px' }}>#</th>
                          <th style={{ padding: '6px' }}>Source</th>
                          <th style={{ padding: '6px' }}>Type</th>
                          <th style={{ padding: '6px' }}>Status</th>
                          <th style={{ padding: '6px' }}>Confidence</th>
                        </tr>
                      </thead>
                      <tbody>
                        {browserSessions.sort((a: any, b: any) => (a.attempt_order || 0) - (b.attempt_order || 0)).map((s: any, sIdx: number) => {
                          const isSuccess = s.status === 'SUCCESS';
                          const statusColor = isSuccess ? '#34d399' : (s.status === 'BLOCKED' || s.status === 'BLOCKED_OR_ERROR' ? '#f87171' : '#f59e0b');
                          
                          return (
                            <tr key={s.id || sIdx} style={{
                              borderBottom: '1px solid rgba(255,255,255,0.04)',
                              background: isSuccess ? 'rgba(52, 211, 153, 0.03)' : 'transparent',
                            }}>
                              <td style={{ padding: '6px' }}>{s.attempt_order || sIdx + 1}</td>
                              <td style={{ padding: '6px' }}>
                                <strong>{s.source_name || s.domain}</strong>
                                {s.url && (
                                  <div style={{ fontSize: '9px', opacity: 0.5, maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {s.url}
                                  </div>
                                )}
                              </td>
                              <td style={{ padding: '6px', textTransform: 'capitalize' }}>{s.source_type}</td>
                              <td style={{ padding: '6px', color: statusColor, fontWeight: 'bold' }}>{s.status}</td>
                              <td style={{ padding: '6px' }}>{(s.confidence * 100).toFixed(0)}%</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Discovered Entities */}
          <div className="glass-panel" style={innerPanelStyle}>
            <h3 style={panelHeaderStyle}>Entity Discovery Candidates ({candidates.length})</h3>
            {candidates.length === 0 ? (
              <div style={panelEmptyStyle}>No candidates discovered yet. Running node...</div>
            ) : (
              <div style={candidatesListStyle}>
                {candidates.map((cand, idx) => {
                  const isResolved = detail.resolved_entity_id !== null && 
                    (cand.gstin === detail.input.gstin || cand.cin === detail.input.cin || cand.name === detail.input.business_name);
                  
                  return (
                    <div key={idx} style={{
                      ...candidateCardStyle,
                      borderColor: isResolved ? 'var(--risk-low)' : 'rgba(255,255,255,0.06)'
                    }}>
                      <div style={candidateCardHeaderStyle}>
                        <strong style={candidateNameStyle}>{cand.name}</strong>
                        {isResolved ? (
                          <span style={resolvedTagStyle}>Resolved</span>
                        ) : (
                          <span style={candidateTagStyle}>Candidate</span>
                        )}
                      </div>
                      <div style={candidateGridStyle}>
                        <span>GSTIN: <code>{cand.gstin || 'N/A'}</code></span>
                        <span>CIN: <code>{cand.cin || 'N/A'}</code></span>
                        <span>Confidence: <code>{((cand.confidence || 0) * 100).toFixed(0)}%</code></span>
                        <span>Location: <code>{cand.location || 'N/A'}</code></span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Evidence Registry */}
          <div className="glass-panel" style={innerPanelStyle}>
            <h3 style={panelHeaderStyle}>Evidence & Sources Log ({evidence.length})</h3>
            {evidence.length === 0 ? (
              <div style={panelEmptyStyle}>No evidence persisted. Awaiting node tasks...</div>
            ) : (
              <div style={evidenceListStyle}>
                {evidence.filter(ev => ev.field_name !== 'candidate_entities').map((ev) => {
                  const isBlocked = ev.confidence === 0.0 || 
                                    ev.field_value === 'UNAVAILABLE' || 
                                    ev.field_value === '' || 
                                    ev.field_value === '{"title": null, "text": ""}';
                  
                  const isLowConfidence = !isBlocked && ev.confidence > 0.0 && ev.confidence < 0.50;
                  
                  let statusTagText = '✓ VERIFIED EVIDENCE';
                  let cardBorderColor = 'rgba(52, 211, 153, 0.2)';
                  let cardBgColor = 'rgba(52, 211, 153, 0.01)';
                  let statusTagColor = '#34d399';

                  if (isBlocked) {
                    statusTagText = '⚠ UNAVAILABLE SOURCE';
                    cardBorderColor = 'rgba(239, 68, 68, 0.2)';
                    cardBgColor = 'rgba(239, 68, 68, 0.01)';
                    statusTagColor = '#f87171';
                  } else if (isLowConfidence) {
                    statusTagText = '⚠ LOW CONFIDENCE';
                    cardBorderColor = 'rgba(245, 158, 11, 0.2)';
                    cardBgColor = 'rgba(245, 158, 11, 0.01)';
                    statusTagColor = '#f59e0b';
                  }

                  // Human-readable formatting of the field value
                  let displayValue = ev.field_value;
                  if (isBlocked) {
                    displayValue = 'This information was unavailable from the source (blocked, empty, or connection failed).';
                  } else if (displayValue && (displayValue.startsWith('{') || displayValue.startsWith('['))) {
                    try {
                      const parsed = JSON.parse(displayValue);
                      if (typeof parsed === 'object') {
                        displayValue = Object.entries(parsed)
                          .map(([k, v]) => `${k.replace('_', ' ')}: ${v}`)
                          .join('\n');
                      }
                    } catch {
                      // fallback to raw value
                    }
                  }

                  return (
                    <div key={ev.id} style={{
                      ...evidenceCardStyle,
                      border: `1px solid ${cardBorderColor}`,
                      background: cardBgColor,
                    }}>
                      <div style={evidenceCardHeaderStyle}>
                        <span style={evidenceFieldStyle}>{ev.field_name.replace('_', ' ')}</span>
                        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                          {isOfficialSource(ev.source_name) ? (
                            <span style={officialSourceTagStyle}>Official Registry</span>
                          ) : (
                            <span style={thirdPartySourceTagStyle}>Third-Party</span>
                          )}
                          <span style={{
                            fontSize: '11px',
                            padding: '2px 8px',
                            borderRadius: '8px',
                            background: cardBorderColor.replace('0.2', '0.1'),
                            color: statusTagColor,
                            border: `1px dashed ${cardBorderColor}`,
                            fontWeight: '600',
                          }}>{statusTagText}</span>
                        </div>
                      </div>
                      <div style={{
                        fontSize: '14px',
                        color: isBlocked ? 'var(--foreground-muted)' : '#fff',
                        fontStyle: isBlocked ? 'italic' : 'normal',
                        whiteSpace: 'pre-wrap',
                        fontFamily: isBlocked ? 'inherit' : 'monospace',
                        lineHeight: '1.4',
                      }}>{displayValue}</div>
                      <div style={evidenceMetaGridStyle}>
                        <span>Source: <strong>{ev.source_name}</strong></span>
                        <span>Confidence: <strong>{(ev.confidence * 100).toFixed(0)}%</strong></span>
                        {ev.retrieved_timestamp && (
                          <span>Fetched: <strong>{new Date(ev.retrieved_timestamp).toLocaleString()}</strong></span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

        </div>

        {/* Right Hand: Risk Metrics & Generated Reports */}
        <div style={rightColStyle}>
          
          {/* Risk Card */}
          {((detail.risk_score !== null && detail.risk_score !== undefined) || risk !== null) && (() => {
            const isInsufficient = (risk as any)?.insufficient_evidence || evidence.filter(ev => ev.field_name !== 'candidate_entities').length === 0;
            const displayScore = isInsufficient ? 'N/A' : (detail.risk_score !== null && detail.risk_score !== undefined 
              ? detail.risk_score 
              : risk?.overall_risk?.score ?? null);
            const displayLevel = isInsufficient ? 'INSUFFICIENT EVIDENCE' : (detail.risk_level || risk?.overall_risk?.level || null);
            const displayColor = isInsufficient ? '#f59e0b' : getRiskColor(displayLevel);
            const displayGlow = isInsufficient ? 'rgba(245, 158, 11, 0.15)' : getRiskGlow(displayLevel);

            return (
              <div className="glass-panel" style={{
                ...riskPanelStyle,
                borderColor: displayColor,
                boxShadow: `0 0 16px ${displayGlow}`
              }}>
                <h3 style={panelHeaderStyle}>Risk Assessment Output</h3>
                <div style={riskScoreContainerStyle}>
                  <div style={scoreCircleStyle}>
                    <span style={{ fontSize: '42px', fontWeight: '900', color: displayColor }}>
                      {displayScore !== null ? displayScore : 'N/A'}
                    </span>
                    <span style={{ fontSize: '12px', color: 'var(--foreground-muted)', fontWeight: '600' }}>SCORE / 100</span>
                  </div>
                  <div style={riskLevelInfoStyle}>
                    <span style={{ fontSize: '13px', color: 'var(--foreground-muted)' }}>Risk Classification</span>
                    <h4 style={{ fontSize: '20px', fontWeight: '800', color: displayColor }}>
                      {displayLevel || 'UNKNOWN'}
                    </h4>
                  </div>
                </div>
                {isInsufficient && (
                  <div style={{
                    marginTop: '16px',
                    padding: '10px',
                    borderRadius: '6px',
                    background: 'rgba(245, 158, 11, 0.05)',
                    border: '1px dashed rgba(245, 158, 11, 0.2)',
                    fontSize: '12px',
                    color: 'var(--foreground-muted)',
                    lineHeight: '1.4',
                  }}>
                    ⚠️ Verification pipeline resolved no external registry or web evidence records. Risk assessment is incomplete due to insufficient evidence.
                  </div>
                )}

                {risk && (
                  <div style={categoryBreakdownStyle}>
                    <h4 style={subHeaderStyle}>Category Score Breakdown</h4>
                    <div style={categoryGridStyle}>
                      {Object.entries(risk.category_scores || {}).map(([category, score]) => (
                        <div key={category} style={categoryBarItemStyle}>
                          <div style={categoryMetaStyle}>
                            <span style={categoryNameLabelStyle}>{category}</span>
                            <span>{score}</span>
                          </div>
                          <div style={categoryBarBgStyle}>
                            <div style={{
                              ...categoryBarFillStyle,
                              width: `${Math.min(score, 100)}%`,
                              background: getRiskColor(displayLevel)
                            }} />
                          </div>
                        </div>
                      ))}
                    </div>

                    {(risk.risk_signals || []).length > 0 && (
                      <div style={signalsListContainerStyle}>
                        <h4 style={subHeaderStyle}>Triggered Risk Signals ({(risk.risk_signals || []).length})</h4>
                        <div style={signalsListStyle}>
                          {(risk.risk_signals || []).map((sig, idx) => (
                            <div key={idx} style={signalCardStyle}>
                              <div style={signalCardHeaderStyle}>
                                <strong>{sig.code}</strong>
                                <span style={{
                                  fontSize: '11px',
                                  padding: '2px 8px',
                                  borderRadius: '8px',
                                  background: 'rgba(239, 68, 68, 0.1)',
                                  color: '#f87171',
                                  border: '1px solid rgba(239, 68, 68, 0.2)'
                                }}>{sig.severity}</span>
                              </div>
                              <p style={signalDescStyle}>{sig.description}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })()}

          {/* Report Viewer */}
          <div className="glass-panel" style={innerPanelStyle}>
            <div style={reportPanelHeaderStyle}>
              <h3 style={panelHeaderStyle}>Intelligence Reports</h3>
              
              {reports.length > 0 && (
                <div style={versionSelectContainerStyle}>
                  <label htmlFor="report-version" style={{ fontSize: '12px', color: 'var(--foreground-muted)' }}>Version:</label>
                  <select
                    id="report-version"
                    value={selectedReportIdx}
                    onChange={(e) => setSelectedReportIdx(Number(e.target.value))}
                    style={selectVersionStyle}
                  >
                    {reports.map((rep, idx) => (
                      <option key={rep.id} value={idx}>
                        v{rep.version} ({rep.qa_status})
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>

            {reports.length === 0 ? (
              <div style={panelEmptyStyle}>No intelligence report generated yet.</div>
            ) : (
              <div style={reportContainerStyle}>
                {/* Report Metadata */}
                <div style={reportMetaGridStyle}>
                  <div>Rule Engine: <strong>v{reports[selectedReportIdx].report.meta?.rule_version || '1.0.0'}</strong></div>
                  <div>Report version: <strong>{reports[selectedReportIdx].version}</strong></div>
                  <div>QA Status: 
                    <span style={{ 
                      marginLeft: '6px',
                      color: reports[selectedReportIdx].qa_status === 'PASS' 
                        ? '#34d399' 
                        : reports[selectedReportIdx].qa_status === 'FAIL' 
                        ? '#f87171' 
                        : '#f59e0b',
                      fontWeight: '700'
                    }}>
                      {reports[selectedReportIdx].qa_status}
                    </span>
                  </div>
                  {reports[selectedReportIdx].report.meta?.generated_at && (
                    <div>Timestamp: <strong>{new Date(reports[selectedReportIdx].report.meta?.generated_at || '').toLocaleString()}</strong></div>
                  )}
                </div>

                {/* Report Content */}
                <div style={reportContentStyle}>
                  <h4 style={reportSecHeaderStyle}>Executive Summary</h4>
                  <div style={reportTextCardStyle}>
                    <p>{reports[selectedReportIdx].report.recommendation || 'No recommendation available.'}</p>
                  </div>

                  <h4 style={reportSecHeaderStyle}>Findings Detail</h4>
                  <div style={reportFindingsListStyle}>
                    {(reports[selectedReportIdx].report.major_findings as unknown as FindingItem[] | undefined)?.map((finding: FindingItem, idx: number) => (
                      <div key={idx} style={findingCardStyle}>
                        <div style={findingCardHeaderStyle}>
                          <strong>{finding.code}</strong>
                          <span>Confidence: {((finding.confidence || 0) * 100).toFixed(0)}%</span>
                        </div>
                        <p style={findingDescStyle}>{finding.description}</p>
                        {finding.evidence_ids && finding.evidence_ids.length > 0 && (
                          <div style={findingEvidenceListStyle}>
                            <span>Evidence link:</span>
                            {finding.evidence_ids.map((eid: string) => (
                              <code key={eid} style={evidenceTagStyle}>{eid}</code>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>

        </div>
      </div>
    </div>
  );
}

// Inline Styles
const loadingContainerStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  minHeight: '100vh',
  color: 'var(--foreground-muted)',
};

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

const breadcrumbStyle: React.CSSProperties = {
  display: 'flex',
};

const topControlsStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
};

const backLinkStyle: React.CSSProperties = {
  color: 'var(--foreground-muted)',
  fontSize: '14px',
};

const pollingIndicatorContainerStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '16px',
};

const pulseIndicatorStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
};

const pulseDotStyle: React.CSSProperties = {
  width: '8px',
  height: '8px',
  borderRadius: '50%',
  background: '#3b82f6',
};

const manualRefreshButtonStyle: React.CSSProperties = {
  padding: '6px 12px',
  fontSize: '12.5px',
  background: 'rgba(255, 255, 255, 0.05)',
  border: '1px solid rgba(255, 255, 255, 0.08)',
  borderRadius: '6px',
};

const caseHeaderStyle: React.CSSProperties = {
  padding: '24px 30px',
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  flexWrap: 'wrap',
  gap: '20px',
};

const headerLeftSectionStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '4px',
};

const headerLabelStyle: React.CSSProperties = {
  fontSize: '11px',
  fontWeight: '700',
  color: 'var(--foreground-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.5px',
};

const caseTitleStyle: React.CSSProperties = {
  fontSize: '24px',
  fontWeight: '800',
  color: '#fff',
  letterSpacing: '-0.5px',
};

const caseIdStyle: React.CSSProperties = {
  fontSize: '12px',
  color: 'var(--foreground-muted)',
};

const headerRightSectionStyle: React.CSSProperties = {
  display: 'flex',
  gap: '30px',
};

const headerMetricStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '6px',
  alignItems: 'flex-start',
};

const metricLabelStyle: React.CSSProperties = {
  fontSize: '11.5px',
  color: 'var(--foreground-muted)',
  fontWeight: '600',
};

const metricValueStyle: React.CSSProperties = {
  fontSize: '14.5px',
  fontWeight: '700',
  color: '#fff',
};

const hitlPanelStyle: React.CSSProperties = {
  padding: '24px',
  borderColor: '#f59e0b',
  background: 'rgba(245, 158, 11, 0.03)',
  display: 'flex',
  flexDirection: 'column',
  gap: '20px',
};

const hitlHeaderStyle: React.CSSProperties = {
  display: 'flex',
  gap: '14px',
  alignItems: 'flex-start',
};

const hitlIconStyle: React.CSSProperties = {
  fontSize: '32px',
};

const hitlTitleStyle: React.CSSProperties = {
  fontSize: '17px',
  fontWeight: '700',
  color: '#fbbf24',
};

const hitlDescStyle: React.CSSProperties = {
  fontSize: '13.5px',
  color: 'var(--foreground-muted)',
};

const hitlBodyStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '12px',
};

const hitlTaskCardStyle: React.CSSProperties = {
  background: 'rgba(255, 255, 255, 0.03)',
  border: '1px solid rgba(255, 255, 255, 0.06)',
  borderRadius: '8px',
  padding: '16px',
  fontSize: '13.5px',
  display: 'flex',
  flexDirection: 'column',
  gap: '8px',
};

const hitlTaskMetaStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
};

const hitlTaskReasonStyle: React.CSSProperties = {
  color: 'var(--foreground)',
};

const hitlActionsContainerStyle: React.CSSProperties = {
  display: 'flex',
};

const resumeButtonStyle: React.CSSProperties = {
  background: '#f59e0b',
  color: '#000',
  width: '100%',
  padding: '14px',
  fontWeight: '700',
};

const errorStyle: React.CSSProperties = {
  background: 'rgba(239, 68, 68, 0.1)',
  border: '1px solid rgba(239, 68, 68, 0.2)',
  color: '#f87171',
  padding: '16px',
  borderRadius: '8px',
};

const twoColumnLayoutGridStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: '1fr 1fr',
  gap: '30px',
  alignItems: 'start',
};

const leftColStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '30px',
};

const rightColStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '30px',
};

const innerPanelStyle: React.CSSProperties = {
  padding: '24px',
  display: 'flex',
  flexDirection: 'column',
  gap: '16px',
};

const panelHeaderStyle: React.CSSProperties = {
  fontSize: '16px',
  fontWeight: '700',
  color: '#fff',
};

const panelEmptyStyle: React.CSSProperties = {
  padding: '20px 0',
  color: 'var(--foreground-muted)',
  fontSize: '14px',
  textAlign: 'center',
};

const specGridStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: '1fr 1fr',
  gap: '16px',
};

const specItemStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '4px',
};

const specLabelStyle: React.CSSProperties = {
  fontSize: '12px',
  color: 'var(--foreground-muted)',
};

const specValueStyle: React.CSSProperties = {
  fontSize: '14px',
  fontWeight: '600',
};

const candidatesListStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '12px',
};

const candidateCardStyle: React.CSSProperties = {
  background: 'rgba(255, 255, 255, 0.02)',
  border: '1px solid',
  borderRadius: '8px',
  padding: '16px',
  display: 'flex',
  flexDirection: 'column',
  gap: '10px',
};

const candidateCardHeaderStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
};

const candidateNameStyle: React.CSSProperties = {
  fontSize: '14.5px',
  color: '#fff',
};

const resolvedTagStyle: React.CSSProperties = {
  background: 'rgba(16, 185, 129, 0.1)',
  color: '#34d399',
  border: '1px solid rgba(16, 185, 129, 0.2)',
  fontSize: '11px',
  padding: '2px 8px',
  borderRadius: '8px',
  fontWeight: '600',
};

const candidateTagStyle: React.CSSProperties = {
  background: 'rgba(107, 114, 128, 0.15)',
  color: '#9ca3af',
  fontSize: '11px',
  padding: '2px 8px',
  borderRadius: '8px',
  fontWeight: '600',
};

const candidateGridStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: '1fr 1fr',
  gap: '8px',
  fontSize: '12.5px',
  color: 'var(--foreground-muted)',
};

const evidenceListStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '16px',
};

const evidenceCardStyle: React.CSSProperties = {
  background: 'rgba(255, 255, 255, 0.02)',
  border: '1px solid rgba(255, 255, 255, 0.05)',
  borderRadius: '8px',
  padding: '16px',
  display: 'flex',
  flexDirection: 'column',
  gap: '10px',
};

const evidenceCardHeaderStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
};

const evidenceFieldStyle: React.CSSProperties = {
  fontSize: '13px',
  fontWeight: '700',
  color: '#60a5fa',
  textTransform: 'uppercase',
};

const officialSourceTagStyle: React.CSSProperties = {
  fontSize: '11px',
  color: '#34d399',
  fontWeight: '600',
};

const thirdPartySourceTagStyle: React.CSSProperties = {
  fontSize: '11px',
  color: '#9ca3af',
  fontWeight: '600',
};

const evidenceValueStyle: React.CSSProperties = {
  fontSize: '14px',
  color: '#fff',
  fontFamily: 'monospace',
};

const evidenceMetaGridStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  fontSize: '11.5px',
  color: 'var(--foreground-muted)',
  flexWrap: 'wrap',
  gap: '8px',
};

const riskPanelStyle: React.CSSProperties = {
  padding: '24px',
  border: '1px solid',
  borderRadius: '12px',
  display: 'flex',
  flexDirection: 'column',
  gap: '20px',
};

const riskScoreContainerStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '30px',
};

const scoreCircleStyle: React.CSSProperties = {
  width: '100px',
  height: '100px',
  borderRadius: '50%',
  background: 'rgba(255, 255, 255, 0.03)',
  border: '1px solid rgba(255, 255, 255, 0.08)',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
};

const riskLevelInfoStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '6px',
};

const categoryBreakdownStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '16px',
};

const subHeaderStyle: React.CSSProperties = {
  fontSize: '13.5px',
  fontWeight: '700',
  color: '#fff',
};

const categoryGridStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '12px',
};

const categoryBarItemStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '6px',
  fontSize: '13px',
};

const categoryMetaStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
};

const categoryNameLabelStyle: React.CSSProperties = {
  textTransform: 'capitalize',
  color: 'var(--foreground-muted)',
};

const categoryBarBgStyle: React.CSSProperties = {
  height: '6px',
  background: 'rgba(255, 255, 255, 0.06)',
  borderRadius: '3px',
  overflow: 'hidden',
};

const categoryBarFillStyle: React.CSSProperties = {
  height: '100%',
  borderRadius: '3px',
  transition: 'width 0.4s ease',
};

const signalsListContainerStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '12px',
  marginTop: '10px',
};

const signalsListStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '10px',
};

const signalCardStyle: React.CSSProperties = {
  background: 'rgba(239, 68, 68, 0.02)',
  border: '1px solid rgba(239, 68, 68, 0.08)',
  borderRadius: '8px',
  padding: '14px',
  display: 'flex',
  flexDirection: 'column',
  gap: '6px',
};

const signalCardHeaderStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  fontSize: '13.5px',
  color: '#fff',
};

const signalDescStyle: React.CSSProperties = {
  fontSize: '13px',
  color: 'var(--foreground-muted)',
  lineHeight: '1.4',
};

const reportPanelHeaderStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  flexWrap: 'wrap',
  gap: '12px',
};

const versionSelectContainerStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
};

const selectVersionStyle: React.CSSProperties = {
  background: 'rgba(255,255,255,0.05)',
  border: '1px solid rgba(255,255,255,0.1)',
  padding: '6px 12px',
  fontSize: '12.5px',
};

const reportContainerStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '20px',
};

const reportMetaGridStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: '1fr 1fr',
  gap: '10px',
  fontSize: '12.5px',
  color: 'var(--foreground-muted)',
  background: 'rgba(255, 255, 255, 0.02)',
  padding: '12px 16px',
  borderRadius: '8px',
};

const reportContentStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '20px',
};

const reportSecHeaderStyle: React.CSSProperties = {
  fontSize: '14px',
  fontWeight: '700',
  color: '#fff',
  borderBottom: '1px solid rgba(255,255,255,0.05)',
  paddingBottom: '8px',
};

const reportTextCardStyle: React.CSSProperties = {
  fontSize: '14px',
  lineHeight: '1.5',
  color: 'var(--foreground-muted)',
};

const reportFindingsListStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '12px',
};

const findingCardStyle: React.CSSProperties = {
  background: 'rgba(255,255,255,0.01)',
  border: '1px solid rgba(255,255,255,0.04)',
  borderRadius: '8px',
  padding: '14px',
  display: 'flex',
  flexDirection: 'column',
  gap: '8px',
};

const findingCardHeaderStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  fontSize: '13.5px',
  color: '#fff',
};

const findingDescStyle: React.CSSProperties = {
  fontSize: '13px',
  color: 'var(--foreground-muted)',
  lineHeight: '1.45',
};

const findingEvidenceListStyle: React.CSSProperties = {
  display: 'flex',
  gap: '8px',
  alignItems: 'center',
  fontSize: '12px',
  color: 'var(--foreground-muted)',
  flexWrap: 'wrap',
};

const evidenceTagStyle: React.CSSProperties = {
  background: 'rgba(59, 130, 246, 0.1)',
  border: '1px solid rgba(59, 130, 246, 0.2)',
  color: '#60a5fa',
  padding: '2px 6px',
  borderRadius: '4px',
};
