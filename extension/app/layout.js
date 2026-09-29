// Making room for the panel without covering the video.
//
// Two situations:
//
//   There is already a free column right of the video (Udemy's course
//   sidebar, YouTube's recommendations in its default layout). The panel sits
//   over that column and the page is not touched: shrinking the page there
//   squeezed Udemy's video to 614px wide.
//
//   The video already reaches the right edge (YouTube theater mode, a page
//   with no sidebar). The page has to get narrower. A margin on <html> is the
//   usual trick and is what v1 did, but it only moves things in normal flow:
//
//     - YouTube's whole app, <ytd-app>, is absolutely positioned against the
//       window, so it ignored the margin and the panel covered a fifth of the
//       video. Making <body> the positioned ancestor fixes that for any site
//       built this way.
//     - Fixed bars (YouTube's masthead) are pinned to the window: they are
//       told where their right edge is.
//     - YouTube picks its column widths from window.innerWidth, not from the
//       space it has, so the video column kept an 853px minimum, overflowed
//       and was centred off the left edge. It is allowed to shrink.
//
// Fixed bars are also stopped at the panel's edge when the panel merely
// overlays a free column, so it never hides YouTube's account menu.
//
// All of it hangs off classes on <html>, so releasing is a single removal.

const STYLE_ID = 'margin-layout-style';
const CLASS = 'margin-reserve'; // the page makes room
const BESIDE = 'margin-beside'; // the panel is open at all (overlay or not)

const CSS = `
html.${CLASS} { margin-right: var(--margin-w) !important; }
html.${CLASS} body { position: relative !important; }
html.${BESIDE} ytd-app #masthead-container {
  left: 0 !important; right: var(--margin-w) !important; width: auto !important;
}
html.${CLASS} ytd-watch-flexy #primary { min-width: 0 !important; }
`;

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = CSS;
  (document.head || document.documentElement).appendChild(style);
}

/** The panel is open and `width` wide; fixed bars stop at its edge either way. */
export function markBeside(width) {
  ensureStyle();
  const html = document.documentElement;
  html.style.setProperty('--margin-w', `${width}px`);
  html.classList.add(BESIDE);
}

export function reserveSpace(width) {
  markBeside(width);
  document.documentElement.classList.add(CLASS);
}

export function releaseSpace() {
  document.documentElement.classList.remove(CLASS, BESIDE);
}

export function isReserved() {
  return document.documentElement.classList.contains(CLASS);
}

export function removeLayoutStyle() {
  releaseSpace();
  document.documentElement.style.removeProperty('--margin-w');
  document.getElementById(STYLE_ID)?.remove();
}

/**
 * How wide the panel should be, and whether the page must make room, given
 * where the video's right edge sits when the page is left alone.
 */
export function planLayout(viewportWidth, videoRight) {
  const free = viewportWidth - videoRight;
  if (free >= 340) return { width: Math.min(460, Math.floor(free)), reserve: false };
  return { width: Math.min(440, Math.max(340, Math.round(viewportWidth * 0.3))), reserve: true };
}
