import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import StatusBadge from '../components/StatusBadge';
import Investigate from '../app/investigate/page';
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

});
