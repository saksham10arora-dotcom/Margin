import { describe, expect, it } from 'vitest';
import { pageTitle } from './mine.js';

describe('the title a page you read is filed under', () => {
  it('drops the site and its tagline', () => {
    expect(pageTitle('Computer Networks | Dotnotes | Notes, Books, PYQs, Akash, Videos')).toBe('Computer Networks');
    expect(pageTitle('Attention Is All You Need - Wikipedia')).toBe('Attention Is All You Need');
  });

  it('keeps a title that has nothing to drop, or would be left too short', () => {
    expect(pageTitle('  Sliding   window  protocol ')).toBe('Sliding window protocol');
    expect(pageTitle('Go | The Go Programming Language')).toBe('Go | The Go Programming Language');
  });
});
