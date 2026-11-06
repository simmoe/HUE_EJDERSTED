import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { evidenceIdFromUrl, indexOfEvidence, parseEvidenceList } from './cameraEvidence.ts';

describe('parseEvidenceList', () => {
  it('keeps newest-first items with a local url', () => {
    const items = parseEvidenceList({
      items: [
        { id: 'person-b', createdAt: 2, url: '/api/security/evidence/person-b.jpg' },
        { id: 'bad/id', createdAt: 3 },
        { id: 'person-a', createdAt: 1 },
      ],
    });
    assert.equal(items.length, 2);
    assert.equal(items[0].id, 'person-b');
    assert.equal(items[1].url, '/api/security/evidence/person-a.jpg');
  });

  it('never keeps a storage url — the hub strips the baked date bar on the local jpeg', () => {
    const items = parseEvidenceList({
      items: [{
        id: 'person-x',
        createdAt: 1,
        url: 'https://firebasestorage.googleapis.com/v0/b/x/o/ejdersted%2Fgarden%2Fevents%2Fperson-x%2Fsnapshot.jpg?alt=media',
      }],
    });
    assert.equal(items[0].url, '/api/security/evidence/person-x.jpg');
  });
});

describe('evidenceIdFromUrl', () => {
  it('reads local and storage urls', () => {
    assert.equal(evidenceIdFromUrl('/api/security/evidence/person-1.jpg'), 'person-1');
    assert.equal(
      evidenceIdFromUrl('https://firebasestorage.googleapis.com/v0/b/x/o/ejdersted%2Fgarden%2Fevents%2Fperson-2%2Fsnapshot.jpg?alt=media'),
      'person-2',
    );
  });
});

describe('indexOfEvidence', () => {
  it('starts on the matching still', () => {
    const items = parseEvidenceList({
      items: [{ id: 'a' }, { id: 'b' }, { id: 'c' }],
    });
    assert.equal(indexOfEvidence(items, '/api/security/evidence/b.jpg'), 1);
  });
});
