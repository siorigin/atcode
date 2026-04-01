'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { Suspense } from 'react';
import { useParams } from 'next/navigation';
import { RepoWorkspace } from '@/components/workspace/RepoWorkspace';

export default function OverviewPage() {
  return (
    <Suspense>
      <OverviewPageInner />
    </Suspense>
  );
}

function OverviewPageInner() {
  const params = useParams();
  const repoName = params.repo as string;
  return <RepoWorkspace repoName={repoName} />;
}
