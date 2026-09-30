import { describe, expect, it } from 'vitest';
import { Quiz } from './quiz.js';

const cards = [{ front: 'A?', back: 'a' }, { front: 'B?', back: 'b' }, { front: 'C?', back: 'c' }];

describe('Quiz', () => {
  it('hides the answer until you flip, and only then takes a grade', () => {
    const q = new Quiz(cards);
    expect(q.current.front).toBe('A?');
    q.grade(true); // not flipped yet: ignored
    expect(q.current.front).toBe('A?');
    q.flip();
    expect(q.flipped).toBe(true);
    q.grade(true);
    expect(q.current.front).toBe('B?');
    expect(q.flipped).toBe(false);
  });

  it('brings a card you missed back at the end until you know it', () => {
    const q = new Quiz(cards);
    q.flip(); q.grade(false); // A missed
    q.flip(); q.grade(true); // B
    q.flip(); q.grade(true); // C
    expect(q.current.front).toBe('A?');
    expect(q.toRepeat).toBe(1);
    q.flip(); q.grade(false); // missed again: still coming back
    expect(q.current.front).toBe('A?');
    q.flip(); q.grade(true);
    expect(q.done).toBe(true);
    expect(q.known).toBe(3);
    expect(q.missed.size).toBe(1);
    expect(q.current).toBe(null);
  });
});
