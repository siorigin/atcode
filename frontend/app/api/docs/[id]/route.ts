// Copyright (c) 2026 SiOrigin Co. Ltd.
// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';
import { promises as fs } from 'fs';
import path from 'path';
import { getWikiDocDir } from '@/lib/paths';

export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id: docId } = await params;

    // Validate doc ID format
    if (!docId || docId.length !== 12 || !/^[a-f0-9]+$/.test(docId)) {
      return NextResponse.json(
        { error: 'Invalid documentation ID format' },
        { status: 400 }
      );
    }

    // Try to read from wiki_doc directory (using centralized config)
    const wikiDocFilePath = path.join(getWikiDocDir(), `${docId}.json`);

    try {
      const fileContents = await fs.readFile(wikiDocFilePath, 'utf8');
      const data = JSON.parse(fileContents);
      return NextResponse.json(data);
    } catch (fileError) {
      // File doesn't exist in wiki_doc directory
      console.log(`Documentation file ${docId}.json not found in wiki_doc directory`);
    }

    // Documentation not found
    return NextResponse.json(
      { error: 'Documentation not found' },
      { status: 404 }
    );

  } catch (error) {
    console.error('Error serving documentation:', error);
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    );
  }
}
