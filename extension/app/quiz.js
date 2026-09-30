// A round of flashcards in the panel: the answer is hidden until you ask for
// it, and a card you did not know comes back at the end of the round, until
// you know them all. Anki does this over weeks; this is the same idea for the
// ten minutes after a lecture, and a way to see what the cards are like.

export class Quiz {
  constructor(cards) {
    this.cards = cards;
    this.queue = cards.map((_, i) => i);
    this.missed = new Set(); // every card you did not know at least once
    this.known = 0;
    this.flipped = false;
  }

  get current() {
    return this.queue.length ? this.cards[this.queue[0]] : null;
  }

  get done() {
    return this.queue.length === 0;
  }

  /** Cards waiting for another go: missed and not yet known. */
  get toRepeat() {
    return this.queue.filter((i) => this.missed.has(i)).length;
  }

  flip() {
    if (!this.done) this.flipped = !this.flipped;
  }

  /** You knew it: it leaves the round. You did not: it comes back at the end. */
  grade(knew) {
    if (this.done || !this.flipped) return;
    const i = this.queue.shift();
    if (knew) this.known += 1;
    else {
      this.missed.add(i);
      this.queue.push(i);
    }
    this.flipped = false;
  }
}
