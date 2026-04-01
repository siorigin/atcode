'use client';

// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

/**
 * Home page - redirects to repos page
 */
export default function HomePage() {
  const router = useRouter();

  useEffect(() => {
    router.replace('/repos');
  }, [router]);

  return null;
}
