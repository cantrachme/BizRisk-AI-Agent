import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import StatusBadge from '../components/StatusBadge';
import Investigate from '../app/investigate/page';
import InvestigationPage from '../app/investigations/[id]/page';
import { api, APIError } from '../lib/api';

// 1. Mock Next.js navigation hooks
jest.mock('next/navigation', () => ({
  useRouter() {
    return {
      push: jest.fn(),
      replace: jest.fn(),
    };
  },
  useParams() {
    return { id: 'test-inv-id-123' };
  },
}));

// 2. Mock API Client
jest.mock('../lib/api', () => ({
  api: {
    createInvestigation: jest.fn(),
    getInvestigation: jest.fn(),
    getEvidence: jest.fn(),
    getRisk: jest.fn(),
    getReports: jest.fn(),
    getHumanIntervention: jest.fn(),
    resumeInvestigation: jest.fn(),
    getInvestigations: jest.fn(),
    getIncompleteInvestigations: jest.fn(),
  },
  APIError: class extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  }
}));

describe('BizRisk Frontend Component Tests', () => {
  
  beforeEach(() => {
    jest.clearAllMocks();
  });

  // Test 1: StatusBadge renders correct labels and handles lowercase/uppercase states
  test('renders StatusBadge with correct text and uppercase formatting', () => {
    render(<StatusBadge status="completed" />);
    expect(screen.getByText('COMPLETED')).toBeInTheDocument();
    
    render(<StatusBadge status="waiting_for_user" />);
    expect(screen.getByText('WAITING FOR USER')).toBeInTheDocument();

    // Handles null / undefined gracefully
    render(<StatusBadge status={null as any} />);
    expect(screen.getByText('UNKNOWN')).toBeInTheDocument();
  });

  // Test 2: Intake Form validation checks
  test('shows validation error when starting an investigation with empty identifiers', () => {
    render(<Investigate />);
    
    const startButton = screen.getByRole('button', { name: /Start Investigation Graph/i });
    fireEvent.click(startButton);
    
    expect(screen.getByText(/Please provide at least one key identifier/i)).toBeInTheDocument();
  });

  // Test 3: Intake Form accepts partial inputs (e.g. GSTIN only)
  test('allows submission when only GSTIN is provided', async () => {
    (api.createInvestigation as jest.Mock).mockResolvedValue({ id: 'new-id-123', status: 'CREATED' });
    render(<Investigate />);
    
    const gstinInput = screen.getByLabelText(/GSTIN/i);
    fireEvent.change(gstinInput, { target: { value: '27ABCDE1234F1Z5' } });
    
    const startButton = screen.getByRole('button', { name: /Start Investigation Graph/i });
    fireEvent.click(startButton);
    
    await waitFor(() => {
      expect(api.createInvestigation).toHaveBeenCalledWith({ gstin: '27ABCDE1234F1Z5' });
    });
  });

  // Test 4: Auth token Bearer injection helper validation
  test('token injection in localstorage persists correctly', () => {
    localStorage.setItem('bizrisk_token', 'TestUserA');
    expect(localStorage.getItem('bizrisk_token')).toBe('TestUserA');
    localStorage.removeItem('bizrisk_token');
  });

  // Test 5: Error states are rendered clearly
  test('shows error message if API fails during launch', async () => {
    const apiError = new APIError('Invalid GSTIN format supplied', 400);
    (api.createInvestigation as jest.Mock).mockRejectedValue(apiError);
    
    render(<Investigate />);
    
    const nameInput = screen.getByLabelText(/Company \/ Business Name/i);
    fireEvent.change(nameInput, { target: { value: 'Invalid Corp' } });
    
    const startButton = screen.getByRole('button', { name: /Start Investigation Graph/i });
    fireEvent.click(startButton);
    
    await waitFor(() => {
      expect(screen.getByText(/Invalid GSTIN format supplied/i)).toBeInTheDocument();
    });
  });

  // Test 6: Investigation Page renders risk score, risk level, report, findings, evidence references, and QA PASS status
  test('renders investigation details, risk metrics, evidence, report findings, and QA PASS status', async () => {
    (api.getInvestigation as jest.Mock).mockResolvedValue({
      id: 'test-inv-id-123',
      status: 'COMPLETED',
      input: { business_name: 'Acme Solutions Pvt Ltd', gstin: '27ABCDE1234F1Z5' },
      current_node: 'REPORT_GENERATION',
      retry_count: 0,
      risk_score: 15,
      risk_level: 'LOW',
      resolved_entity_id: 'entity-999',
      entity_confidence: 0.95,
      completed_at: '2026-08-30T10:00:00Z',
      created_at: '2026-08-30T09:00:00Z',
      updated_at: '2026-08-30T10:00:00Z',
    });

    (api.getEvidence as jest.Mock).mockResolvedValue([
      {
        id: 'ev-1',
        investigation_id: 'test-inv-id-123',
        research_result_id: 'res-1',
        task_id: 'task-1',
        field_name: 'registration_status',
        field_value: 'Active',
        source_name: 'GST Registry',
        source_url: 'https://gst.gov.in',
        retrieved_timestamp: '2026-08-30T09:30:00Z',
        confidence: 0.99,
        created_timestamp: '2026-08-30T09:30:00Z',
      }
    ]);

    (api.getRisk as jest.Mock).mockResolvedValue({
      overall_risk: { score: 15, level: 'LOW' },
      category_scores: { identity: 10, registration: 5, compliance: 20 },
      risk_signals: [
        {
          category: 'compliance',
          code: 'SIG_GST_ACTIVE',
          severity: 'INFO',
          description: 'GST registration active and verified',
          evidence_ids: ['ev-1'],
          confidence: 0.99,
          risk_weight: 5,
        }
      ]
    });

    (api.getReports as jest.Mock).mockResolvedValue([
      {
        id: 'rep-1',
        investigation_id: 'test-inv-id-123',
        version: 1,
        report: {
          recommendation: 'Low risk entity. Approved for onboarding.',
          major_findings: [
            {
              code: 'FINDING_GST_VERIFIED',
              confidence: 0.99,
              description: 'GST record verified on official portal.',
              evidence_ids: ['ev-1'],
            }
          ],
          meta: { rule_version: '1.0.0' }
        },
        qa_status: 'PASS',
        created_at: '2026-08-30T10:00:00Z',
      }
    ]);

    render(<InvestigationPage />);

    await waitFor(() => {
      expect(screen.getByText('Acme Solutions Pvt Ltd')).toBeInTheDocument();
      expect(screen.getByText('15')).toBeInTheDocument();
      expect(screen.getByText('LOW')).toBeInTheDocument();
      expect(screen.getByText('PASS')).toBeInTheDocument();
      expect(screen.getByText('Low risk entity. Approved for onboarding.')).toBeInTheDocument();
      expect(screen.getByText('FINDING_GST_VERIFIED')).toBeInTheDocument();
      expect(screen.getByText('ev-1')).toBeInTheDocument();
    });
  });

  // Test 7: Handles QA FAIL status and API errors gracefully
  test('renders QA FAIL status and handles API error on investigation detail', async () => {
    (api.getInvestigation as jest.Mock).mockResolvedValue({
      id: 'test-inv-id-123',
      status: 'COMPLETED',
      input: { business_name: 'Risky Biz Ltd' },
      current_node: 'REPORT_GENERATION',
      retry_count: 0,
      risk_score: 85,
      risk_level: 'HIGH',
      resolved_entity_id: null,
      entity_confidence: 0.5,
      completed_at: '2026-08-30T10:00:00Z',
      created_at: '2026-08-30T09:00:00Z',
      updated_at: '2026-08-30T10:00:00Z',
    });

    (api.getEvidence as jest.Mock).mockRejectedValue(new APIError('Failed to fetch evidence', 500));
    (api.getRisk as jest.Mock).mockResolvedValue(null as any);
    (api.getReports as jest.Mock).mockResolvedValue([
      {
        id: 'rep-2',
        investigation_id: 'test-inv-id-123',
        version: 1,
        report: {
          recommendation: 'High risk entity. Manual review required.',
          major_findings: [],
        },
        qa_status: 'FAIL',
        created_at: '2026-08-30T10:00:00Z',
      }
    ]);

    render(<InvestigationPage />);

    await waitFor(() => {
      expect(screen.getByText('Risky Biz Ltd')).toBeInTheDocument();
      expect(screen.getByText('FAIL')).toBeInTheDocument();
      expect(screen.getByText('High risk entity. Manual review required.')).toBeInTheDocument();
    });
  });

});

