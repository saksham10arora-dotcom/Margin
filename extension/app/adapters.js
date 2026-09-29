// What platform is this, which lecture is playing, and where are its words?
//
// Everything site-specific lives here, and every adapter answers the same two
// questions, so the rest of Margin never mentions a site by name:
//
//   lectureInfo()  -> {platform, course_id, course_title, section_title,
//                      section_index, lecture_id, lecture_title,
//                      lecture_index, duration_sec, url, author}
//   captions()     -> {cues: [{start, end, text}], source, language} or null
//
// When captions() returns null, Margin listens to the audio instead, so an
// adapter never has to be complete for Margin to work.

import { cuesFromStarts, parseVtt, pickCaption } from './util.js';

export function detectPlatform(loc = location) {
  const host = loc.hostname;
  if (/(^|\.)youtube\.com$/.test(host)) return 'youtube';
  if (/(^|\.)udemy\.com$/.test(host)) return 'udemy';
  if (/(^|\.)coursera\.org$/.test(host)) return 'coursera';
  if (loc.protocol === 'file:') return 'local';
  return 'web';
}

/** The lecture's own <video>: the biggest one that is actually showing. */
export function findVideo(doc = document) {
  const videos = [...doc.querySelectorAll('video')];
  let best = null;
  let bestArea = 0;
  for (const v of videos) {
    const r = v.getBoundingClientRect();
    const area = r.width * r.height;
    if (area > bestArea && r.width > 200) {
      best = v;
      bestArea = area;
    }
  }
  return best;
}

const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

// --- YouTube -----------------------------------------------------------------

/**
 * The title of THIS video. Read too early, YouTube's page title is just
 * "YouTube", and after an in-page navigation the heading still shows the
 * previous video for a moment; either would name the note after the wrong
 * thing. So wait until the player says it is showing this video id, then
 * take the heading, with the tab title as a fallback.
 */
async function youtubeTitle(id) {
  const fromTab = () => clean(document.title.replace(/^\(\d+\)\s*/, '').replace(/ - YouTube$/, ''));
  for (let i = 0; i < 40; i++) {
    const current = document.querySelector('ytd-watch-flexy')?.getAttribute('video-id');
    if (current === id) {
      const heading = clean(document.querySelector('h1.ytd-watch-metadata yt-formatted-string')?.textContent);
      if (heading) return heading;
      const tab = fromTab();
      if (tab && tab !== 'YouTube') return tab;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  const tab = fromTab();
  return tab && tab !== 'YouTube' ? tab : `YouTube video ${id}`;
}


const youtube = {
  lectureKey: () => (location.pathname === '/watch' ? new URLSearchParams(location.search).get('v') : null),

  // Ads play in the lecture's own <video> element. While one is on, nothing
  // it shows or says belongs in the notes, and its time is not lecture time.
  isAd: () => Boolean(document.querySelector('#movie_player.ad-showing, #movie_player.ad-interrupting')),

  async lectureInfo(video) {
    const params = new URLSearchParams(location.search);
    const id = params.get('v');
    const title = await youtubeTitle(id);
    // A playlist someone made (PL) or an official course (OL) is a course: its
    // videos share a folder, numbering, an index and a notebook. A Mix (RD),
    // Watch later (WL) or your Liked videos (LL) are not.
    const listId = params.get('list');
    const isCourse = /^(PL|OL)[\w-]+$/.test(listId || '');
    let playlist = null;
    // The playlist panel draws after the video; read too early, one video
    // would be filed once as standalone and once as part of the course.
    for (let i = 0; isCourse && !playlist && i < 20; i++) {
      playlist = clean(document.querySelector('ytd-playlist-panel-renderer #header-description h3 a, '
        + 'ytd-playlist-panel-renderer .title')?.textContent) || null;
      if (!playlist) await new Promise((r) => setTimeout(r, 200));
    }
    const items = [...document.querySelectorAll('ytd-playlist-panel-renderer ytd-playlist-panel-video-renderer')];
    const position = items.findIndex((el) => el.hasAttribute('selected')) + 1;
    // The panel's selected row, not the URL: YouTube rewrites `index=` ahead of
    // the video actually playing (index=2 while the first one plays).
    const index = position || Number(params.get('index')) || null;
    return {
      platform: 'youtube',
      course_id: playlist ? `yt-${listId}` : null,
      course_title: playlist,
      lecture_id: id,
      lecture_title: title,
      lecture_index: playlist ? index : null,
      duration_sec: video && Number.isFinite(video.duration) ? Math.round(video.duration) : null,
      url: `https://www.youtube.com/watch?v=${id}${playlist ? `&list=${listId}` : ''}`,
      author: clean(document.querySelector('#channel-name a')?.textContent) || null,
    };
  },

  async captions(meta, bridge) {
    // YouTube's timedtext needs a proof-of-origin token from a page, so the
    // sidecar fetches it with youtube_transcript_api instead (see v1 notes).
    const res = await bridge.api('GET', `/transcript?video_id=${encodeURIComponent(meta.lecture_id)}`);
    if (!res.ok || !res.data?.cues?.length) return null;
    return { cues: cuesFromStarts(res.data.cues), source: 'youtube-captions', language: res.data.language };
  },
};

// --- Udemy -------------------------------------------------------------------

const udemyCurriculum = new Map(); // courseId -> Promise<{title, lectures: Map}>

function udemyCourseId() {
  try {
    const args = document.querySelector('[data-module-id="course-taking"]')?.dataset?.moduleArgs;
    const id = args ? JSON.parse(args).courseId : null;
    if (id) return String(id);
  } catch { /* fall through */ }
  return null;
}

async function udemyJson(path) {
  const r = await fetch(path, { credentials: 'include' });
  if (!r.ok) throw new Error(`Udemy API ${r.status}`);
  return r.json();
}

/** Links and files a lecture lists under Resources (a course repo, slides). */
function udemyResources(item) {
  return (item.supplementary_assets || []).map((a) => ({
    title: clean(a.title || a.filename || ''),
    url: a.asset_type === 'ExternalLink' && /^https?:\/\//.test(a.external_url || '') ? a.external_url : null,
    kind: a.asset_type === 'ExternalLink' ? 'link' : 'file',
  })).filter((r) => r.title);
}

function loadUdemyCourse(courseId) {
  if (!udemyCurriculum.has(courseId)) {
    udemyCurriculum.set(courseId, (async () => {
      const items = [];
      let next = `/api-2.0/courses/${courseId}/subscriber-curriculum-items/?page_size=1400`
        + '&fields[lecture]=title,object_index,supplementary_assets,asset&fields[chapter]=title,object_index'
        + '&fields[quiz]=title,object_index&fields[practice]=title,object_index'
        + '&fields[asset]=title,filename,asset_type,external_url,time_estimation';
      const coursePromise = udemyJson(`/api-2.0/courses/${courseId}/?fields[course]=title`);
      // One page holds most courses; the biggest run past it.
      for (let page = 0; next && page < 10; page++) {
        const cur = await udemyJson(next);
        items.push(...(cur.results || []));
        next = cur.next ? new URL(cur.next).pathname + new URL(cur.next).search : null;
      }
      const course = await coursePromise;
      const lectures = new Map();
      const courseLinks = [];
      let chapter = null;
      for (const item of items) {
        if (item._class === 'chapter') chapter = item;
        if (item._class === 'lecture') {
          const resources = udemyResources(item);
          lectures.set(String(item.id), {
            title: item.title, index: item.object_index,
            section: chapter?.title ?? null, sectionIndex: chapter?.object_index ?? null,
            resources,
            duration: item.asset?.time_estimation ?? null,
            video: item.asset?.asset_type === 'Video',
          });
          for (const r of resources) {
            if (r.url && !courseLinks.some((c) => c.url === r.url)) courseLinks.push(r);
          }
        }
      }
      return { title: course.title, lectures, links: courseLinks.slice(0, 8) };
    })().catch((e) => {
      udemyCurriculum.delete(courseId); // retry next time rather than caching a failure
      throw e;
    }));
  }
  return udemyCurriculum.get(courseId);
}

// Which lectures you have completed changes slowly; the Course tab refreshes
// often while notes are being written. Asked at most every two minutes.
const udemyProgressCache = new Map(); // courseId -> { at, promise }
function udemyProgress(courseId) {
  const hit = udemyProgressCache.get(courseId);
  if (hit && Date.now() - hit.at < 120000) return hit.promise;
  const promise = udemyJson(`/api-2.0/users/me/subscribed-courses/${courseId}/progress/?fields[course]=completed_lecture_ids`);
  promise.catch(() => udemyProgressCache.delete(courseId));
  udemyProgressCache.set(courseId, { at: Date.now(), promise });
  return promise;
}

/** What a note needs about one lecture of a loaded course. */
function udemyLectureMeta(courseId, course, lectureId, lec) {
  const slug = location.pathname.match(/\/course\/([^/]+)/)?.[1];
  return {
    platform: 'udemy', course_id: courseId, lecture_id: String(lectureId),
    url: slug ? `${location.origin}/course/${slug}/learn/lecture/${lectureId}` : location.href.split('#')[0],
    duration_sec: lec?.duration ?? null,
    course_title: course.title,
    lecture_title: lec?.title ?? null,
    lecture_index: lec?.index ?? null,
    section_title: lec?.section ?? null,
    section_index: lec?.sectionIndex ?? null,
    resources: lec?.resources ?? [],
    // A course repo is often attached to the first lecture only, yet every
    // lecture's "open the notebook in the repo" means it.
    course_links: course.links,
  };
}

const udemy = {
  lectureKey: () => location.pathname.match(/\/learn\/lecture\/(\d+)/)?.[1] ?? null,

  /**
   * Video lectures you have marked complete on Udemy, in course order: the
   * ones you watched, perhaps before Margin, and can have notes for from
   * their captions.
   */
  async finishedLectures() {
    const courseId = udemyCourseId();
    if (!courseId) return [];
    const [course, progress] = await Promise.all([loadUdemyCourse(courseId), udemyProgress(courseId)]);
    const done = new Set((progress.completed_lecture_ids || []).map(String));
    return [...course.lectures.entries()]
      .filter(([id, lec]) => done.has(id) && lec.video)
      .map(([id, lec]) => udemyLectureMeta(courseId, course, id, lec))
      .sort((a, b) => (a.lecture_index ?? 1e9) - (b.lecture_index ?? 1e9));
  },

  async lectureInfo(video) {
    const lectureId = udemy.lectureKey();
    const courseId = udemyCourseId();
    const base = {
      platform: 'udemy', course_id: courseId, lecture_id: lectureId,
      url: location.href.split('#')[0].split('?')[0],
      duration_sec: video && Number.isFinite(video.duration) ? Math.round(video.duration) : null,
    };
    if (!courseId || !lectureId) return { ...base, lecture_title: clean(document.title) };
    try {
      const course = await loadUdemyCourse(courseId);
      const meta = udemyLectureMeta(courseId, course, lectureId, course.lectures.get(lectureId));
      return { ...meta, url: base.url, duration_sec: base.duration_sec ?? meta.duration_sec };
    } catch {
      return { ...base, lecture_title: clean(document.title) };
    }
  },

  async captions(meta, bridge) {
    if (!meta.course_id || !meta.lecture_id) return null;
    const lec = await udemyJson(`/api-2.0/users/me/subscribed-courses/${meta.course_id}/lectures/`
      + `${meta.lecture_id}/?fields[lecture]=title,asset&fields[asset]=captions,time_estimation`);
    const tracks = (lec.asset?.captions || []).map((c) => ({
      locale: c.locale_id, source: c.source, url: c.url, label: c.video_label,
    }));
    const pick = pickCaption(tracks);
    if (!pick?.url) return null;
    let text;
    try {
      const r = await fetch(pick.url);
      text = r.ok ? await r.text() : null;
    } catch { text = null; }
    if (!text) {
      const res = await bridge.send({ type: 'fetch-text', url: pick.url });
      text = res?.ok ? res.text : null;
    }
    const cues = text ? parseVtt(text) : [];
    if (!cues.length) return null;
    return {
      cues,
      source: pick.source === 'auto' ? 'udemy-auto-captions' : 'udemy-captions',
      language: pick.locale,
    };
  },
};

// --- Anything else with a <video> ---------------------------------------------

/** Cues from the player's own <track> elements (Coursera, most HTML5 players). */
async function textTrackCues(video) {
  if (!video || !video.textTracks?.length) return null;
  const tracks = [...video.textTracks].filter((t) => t.kind === 'subtitles' || t.kind === 'captions');
  if (!tracks.length) return null;
  const pick = pickCaption(tracks.map((t, i) => ({ locale: t.language, source: 'manual', url: i })));
  const track = tracks[pick.url];
  const previous = track.mode;
  if (track.mode === 'disabled') track.mode = 'hidden'; // loads cues without showing them
  for (let i = 0; i < 30 && !(track.cues?.length); i++) await new Promise((r) => setTimeout(r, 100));
  const cues = [...(track.cues || [])].map((c) => ({
    start: c.startTime, end: c.endTime, text: clean(c.text?.replace(/<[^>]+>/g, '') ?? ''),
  })).filter((c) => c.text);
  if (previous === 'disabled') track.mode = 'disabled';
  return cues.length ? { cues, source: 'page-captions', language: track.language || null } : null;
}

const generic = {
  lectureKey: () => location.pathname + location.search,

  async lectureInfo(video) {
    const og = (p) => document.querySelector(`meta[property="${p}"]`)?.content;
    const title = clean(og('og:title')) || clean(document.querySelector('h1')?.textContent) || clean(document.title);
    const platform = detectPlatform();
    const src = platform === 'local' ? decodeURIComponent(location.pathname.split('/').pop() || '') : '';
    return {
      platform,
      lecture_id: platform === 'local' ? location.pathname : `${location.host}${location.pathname}`,
      lecture_title: src ? src.replace(/\.[a-z0-9]+$/i, '') : title,
      duration_sec: video && Number.isFinite(video.duration) ? Math.round(video.duration) : null,
      url: location.href.split('#')[0],
    };
  },

  async captions(_meta, _bridge, video) {
    return textTrackCues(video);
  },
};

export function adapterFor(platform) {
  if (platform === 'youtube') return youtube;
  if (platform === 'udemy') return udemy;
  return generic;
}
