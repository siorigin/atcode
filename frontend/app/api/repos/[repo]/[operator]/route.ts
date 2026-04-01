// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';
import { promises as fs } from 'fs';
import path from 'path';
import { getWikiDocDir } from '@/lib/paths';

/**
 * API endpoint to get full documentation for a specific operator
 * Returns: Full documentation JSON
 */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ repo: string; operator: string }> }
) {
  try {
    const { repo: repoName, operator: operatorName } = await params;

    // Path to operator documentation (using centralized config)
    const filePath = path.join(
      getWikiDocDir(repoName),
      `${operatorName}.json`
    );

    // Check if file exists
    try {
      await fs.access(filePath);
    } catch {
      return NextResponse.json(
        { error: 'Operator documentation not found' },
        { status: 404 }
      );
    }

    // Read and return full documentation
    const content = await fs.readFile(filePath, 'utf8');
    const data = JSON.parse(content);

    return NextResponse.json(data);
  } catch (error) {
    console.error('Error reading operator documentation:', error);
    return NextResponse.json(
      { error: 'Failed to read documentation' },
      { status: 500 }
    );
  }
}

export async function DELETE(
  request: Request,
  { params }: { params: Promise<{ repo: string; operator: string }> }
) {
  try {
    const { repo, operator } = await params;

    if (!repo || !operator) {
      return NextResponse.json(
        { error: 'Repository and operator names are required' },
        { status: 400 }
      );
    }

    // Path to the documentation file (using centralized config)
    const docsPath = path.join(getWikiDocDir(repo), `${operator}.json`);

    try {
      // Check if file exists
      await fs.access(docsPath);
      
      // Delete the file
      await fs.unlink(docsPath);

      return NextResponse.json({
        success: true,
        message: 'Operator documentation deleted successfully'
      });
    } catch (error: any) {
      if (error.code === 'ENOENT') {
        return NextResponse.json(
          { error: 'Operator documentation not found' },
          { status: 404 }
        );
      }
      throw error;
    }
  } catch (error: any) {
    console.error('Error deleting operator documentation:', error);
    return NextResponse.json(
      {
        error: 'Failed to delete operator documentation',
        details: error.message
      },
      { status: 500 }
    );
  }
}

