'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { useTheme } from '@/lib/theme-context';
import { GenerationJob } from '@/lib/store';

export default function ProgressPage() {
  const router = useRouter();
  const params = useParams();
  const jobId = params.jobId as string;
  const { theme } = useTheme();
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!jobId) return;

    // Initial fetch
    fetchJobStatus();

    // Poll for updates every 2 seconds
    const interval = setInterval(fetchJobStatus, 2000);

    return () => clearInterval(interval);
  }, [jobId]);

  async function fetchJobStatus() {
    try {
      // Try unified task API first (for new UUID-based task IDs)
      // Fall back to legacy endpoints for old-style prefixed job IDs
      let endpoint: string;
      let response: Response;

      // Check if it's a legacy job ID with prefix
      if (jobId.startsWith('repo_')) {
        endpoint = `/api/add-repo?jobId=${jobId}`;
        response = await fetch(endpoint);
      } else if (jobId.startsWith('regen_')) {
        endpoint = `/api/regenerate-repo?jobId=${jobId}`;
        response = await fetch(endpoint);
      } else if (jobId.startsWith('operator_')) {
        endpoint = `/api/add-operator?jobId=${jobId}`;
        response = await fetch(endpoint);
      } else {
        // New UUID-based task ID - use unified task API
        endpoint = `/api/tasks/${encodeURIComponent(jobId)}`;
        response = await fetch(endpoint);
      }
      
      if (!response.ok) {
        throw new Error('Failed to fetch job status');
      }

      const rawData = await response.json();

      // Normalize data format - handle both legacy and new unified task API formats
      let data: any;
      if (rawData.task_id) {
        // New unified task API format
        data = {
          id: rawData.task_id,
          type: rawData.task_type === 'graph_build' ? 'repo' :
                rawData.task_type === 'doc_gen' ? 'regen' : 'operator',
          name: rawData.repo_name || 'unknown',
          repoName: rawData.repo_name,
          status: rawData.status,
          progress: rawData.progress,
          currentStep: rawData.status_message || rawData.step,
          error: rawData.error,
          startTime: rawData.created_at ? new Date(rawData.created_at).getTime() : Date.now(),
          endTime: rawData.completed_at ? new Date(rawData.completed_at).getTime() : undefined,
          logs: rawData.status_message ? [rawData.status_message] : [],
        };
      } else {
        // Legacy format - use as is
        data = rawData;
      }

      setJob(data);
      setLoading(false);

      // If job is completed or failed, redirect after a delay
      if (data.status === 'completed' || data.status === 'completed_with_errors') {
        setTimeout(() => {
          const taskType = rawData.task_type || data.type;
          if (taskType === 'repo' || taskType === 'graph_build') {
            // For add-repo jobs, redirect to repos page
            router.push('/repos');
          } else if (taskType === 'regen' || taskType === 'doc_gen') {
            // For regeneration, go to the specific repo's page
            router.push(`/repos/${data.name || data.repoName}`);
          } else {
            router.push(`/repos/${data.repoName || data.name}`);
          }
        }, 2000);
      }
    } catch (err: any) {
      console.error('Error fetching job status:', err);
      setError(err.message);
      setLoading(false);
    }
  }

  if (loading && !job) {
    return (
      <div style={{ 
        background: theme === 'dark' ? '#0e0e0e' : '#ffffff',
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center'
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{
            width: '48px',
            height: '48px',
            borderTop: '3px solid #3b82f6',
            borderRight: `3px solid ${theme === 'dark' ? '#2d3748' : '#e2e8f0'}`,
            borderBottom: `3px solid ${theme === 'dark' ? '#2d3748' : '#e2e8f0'}`,
            borderLeft: `3px solid ${theme === 'dark' ? '#2d3748' : '#e2e8f0'}`,
            borderRadius: '50%',
            animation: 'spin 1s linear infinite',
            margin: '0 auto 16px'
          }} />
          <p style={{ color: theme === 'dark' ? '#718096' : '#a0aec0' }}>Loading progress...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ 
        background: theme === 'dark' ? '#0e0e0e' : '#ffffff',
        minHeight: '100vh',
        padding: '48px',
        color: theme === 'dark' ? '#e2e8f0' : '#1a202c'
      }}>
        <div style={{
          maxWidth: '800px',
          margin: '0 auto',
          padding: '24px',
          background: theme === 'dark' ? '#7f1d1d' : '#fee2e2',
          border: `1px solid ${theme === 'dark' ? '#991b1b' : '#fca5a5'}`,
          borderRadius: '12px',
          color: theme === 'dark' ? '#fca5a5' : '#991b1b'
        }}>
          <h2 style={{ fontSize: '20px', fontWeight: '600', marginBottom: '12px' }}>Error</h2>
          <p>{error}</p>
          <button
            onClick={() => router.back()}
            style={{
              marginTop: '16px',
              padding: '10px 20px',
              background: theme === 'dark' ? '#2d3748' : '#f7fafc',
              border: 'none',
              borderRadius: '8px',
              cursor: 'pointer',
              color: theme === 'dark' ? '#e2e8f0' : '#1a202c'
            }}
          >
            Go Back
          </button>
        </div>
      </div>
    );
  }

  if (!job) return null;

  const isRunning = job.status === 'running' || job.status === 'pending';
  const isCompleted = job.status === 'completed';
  const isFailed = job.status === 'failed';

  return (
    <div style={{ 
      background: theme === 'dark' ? '#0e0e0e' : '#ffffff',
      color: theme === 'dark' ? '#e2e8f0' : '#1a202c',
      minHeight: '100vh',
      fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    }}>
      {/* Header */}
      <header style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '16px 32px',
        borderBottom: `1px solid ${theme === 'dark' ? '#2d3748' : '#e2e8f0'}`
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <button
            onClick={() => router.back()}
            style={{
              background: 'transparent',
              border: '1px solid transparent',
              fontSize: '16px',
              cursor: 'pointer',
              padding: '12px',
              color: theme === 'dark' ? '#e2e8f0' : '#1a202c',
              borderRadius: '8px',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              transition: 'all 0.2s',
            }}
            title="Go back"
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = theme === 'dark' ? '#2d3748' : '#f7fafc';
              e.currentTarget.style.borderColor = theme === 'dark' ? '#4a5568' : '#e2e8f0';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'transparent';
              e.currentTarget.style.borderColor = 'transparent';
            }}
          >
            <svg width="20" height="20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            <span style={{ fontWeight: '500' }}>Back</span>
          </button>
          <h1 style={{ fontSize: '20px', fontWeight: 'bold' }}>AtCode</h1>
          <span style={{
            fontSize: '14px',
            color: theme === 'dark' ? '#718096' : '#a0aec0',
            background: theme === 'dark' ? '#2d3748' : '#f7fafc',
            padding: '4px 12px',
            borderRadius: '6px',
            border: `1px solid ${theme === 'dark' ? '#4a5568' : '#e2e8f0'}`
          }}>
            Progress
          </span>
        </div>
      </header>

      {/* Main Content */}
      <main style={{
        maxWidth: '1200px',
        margin: '0 auto',
        padding: '48px 32px'
      }}>
        {/* Status Header */}
        <div style={{
          background: theme === 'dark' ? '#1a202c' : '#ffffff',
          borderRadius: '16px',
          padding: '32px',
          border: `1px solid ${theme === 'dark' ? '#2d3748' : '#e2e8f0'}`,
          marginBottom: '24px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '24px' }}>
            {isRunning && (
              <div style={{
                width: '48px',
                height: '48px',
                borderTop: '3px solid #3b82f6',
                borderRight: `3px solid ${theme === 'dark' ? '#2d3748' : '#e2e8f0'}`,
                borderBottom: `3px solid ${theme === 'dark' ? '#2d3748' : '#e2e8f0'}`,
                borderLeft: `3px solid ${theme === 'dark' ? '#2d3748' : '#e2e8f0'}`,
                borderRadius: '50%',
                animation: 'spin 1s linear infinite'
              }} />
            )}
            {isCompleted && (
              <div style={{
                width: '48px',
                height: '48px',
                background: '#10b981',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '24px',
                color: '#ffffff'
              }}>
                ✓
              </div>
            )}
            {isFailed && (
              <div style={{
                width: '48px',
                height: '48px',
                background: '#ef4444',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '24px',
                color: '#ffffff'
              }}>
                ✗
              </div>
            )}
            <div style={{ flex: 1 }}>
              <h2 style={{ fontSize: '24px', fontWeight: '600', marginBottom: '8px' }}>
                {job.type === 'repo' ? 'Adding Repository' : 'Generating Documentation'}
              </h2>
              <p style={{ fontSize: '18px', color: theme === 'dark' ? '#cbd5e0' : '#4a5568' }}>
                {job.name}
              </p>
              {job.repoName && (
                <p style={{ fontSize: '14px', color: theme === 'dark' ? '#718096' : '#a0aec0', marginTop: '4px' }}>
                  Repository: {job.repoName}
                </p>
              )}
            </div>
            <div style={{
              padding: '8px 16px',
              background: isCompleted ? '#10b981' : isFailed ? '#ef4444' : '#3b82f6',
              color: '#ffffff',
              borderRadius: '8px',
              fontSize: '14px',
              fontWeight: '500'
            }}>
              {job.status.toUpperCase()}
            </div>
          </div>

          <div style={{ fontSize: '14px', color: theme === 'dark' ? '#718096' : '#a0aec0' }}>
            {job.currentStep || 'Initializing...'}
          </div>
        </div>

        {/* Command Section */}
        {job.command && (
          <div style={{
            background: theme === 'dark' ? '#1a202c' : '#ffffff',
            borderRadius: '16px',
            padding: '24px',
            border: `1px solid ${theme === 'dark' ? '#2d3748' : '#e2e8f0'}`,
            marginBottom: '24px'
          }}>
            <h3 style={{ fontSize: '18px', fontWeight: '600', marginBottom: '16px' }}>
              Executing Command
            </h3>
            <div style={{
              background: theme === 'dark' ? '#0e0e0e' : '#f7fafc',
              padding: '16px',
              borderRadius: '8px',
              fontFamily: 'monospace',
              fontSize: '14px',
              color: theme === 'dark' ? '#10b981' : '#059669',
              overflowX: 'auto',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all'
            }}>
              {job.command}
            </div>
          </div>
        )}

        {/* Logs Section */}
        {job.logs && job.logs.length > 0 && (
          <div style={{
            background: theme === 'dark' ? '#1a202c' : '#ffffff',
            borderRadius: '16px',
            padding: '24px',
            border: `1px solid ${theme === 'dark' ? '#2d3748' : '#e2e8f0'}`
          }}>
            <h3 style={{ fontSize: '18px', fontWeight: '600', marginBottom: '16px' }}>
              Execution Logs
            </h3>
            <div style={{
              background: theme === 'dark' ? '#0e0e0e' : '#f7fafc',
              padding: '16px',
              borderRadius: '8px',
              fontFamily: 'monospace',
              fontSize: '13px',
              color: theme === 'dark' ? '#cbd5e0' : '#2d3748',
              maxHeight: '400px',
              overflowY: 'auto',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word'
            }}>
              {job.logs.map((log, index) => (
                <div key={index} style={{ marginBottom: '4px' }}>
                  {log}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Error Section */}
        {job.error && (
          <div style={{
            background: theme === 'dark' ? '#7f1d1d' : '#fee2e2',
            borderRadius: '16px',
            padding: '24px',
            border: `1px solid ${theme === 'dark' ? '#991b1b' : '#fca5a5'}`,
            marginTop: '24px'
          }}>
            <h3 style={{ 
              fontSize: '18px', 
              fontWeight: '600', 
              marginBottom: '12px',
              color: theme === 'dark' ? '#fca5a5' : '#991b1b'
            }}>
              Error
            </h3>
            <p style={{ color: theme === 'dark' ? '#fca5a5' : '#991b1b' }}>
              {job.error}
            </p>
          </div>
        )}

        {/* Completion Message */}
        {isCompleted && (
          <div style={{
            background: theme === 'dark' ? '#064e3b' : '#d1fae5',
            borderRadius: '16px',
            padding: '24px',
            border: `1px solid ${theme === 'dark' ? '#059669' : '#6ee7b7'}`,
            marginTop: '24px',
            textAlign: 'center'
          }}>
            <p style={{ 
              fontSize: '16px',
              color: theme === 'dark' ? '#6ee7b7' : '#059669',
              marginBottom: '8px'
            }}>
              ✓ {job.type === 'repo' ? 'Repository added successfully!' : 'Documentation generated successfully!'}
            </p>
            <p style={{ 
              fontSize: '14px',
              color: theme === 'dark' ? '#6ee7b7' : '#059669'
            }}>
              Redirecting...
            </p>
          </div>
        )}
      </main>

      <style jsx>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
