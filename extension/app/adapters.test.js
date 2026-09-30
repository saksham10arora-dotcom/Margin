import { describe, expect, it } from 'vitest';
import { detectPlatform, dlaiFinished, dlaiLessonMeta, dlaiPath, dlaiTracks } from './adapters.js';

// The shape learn.deeplearning.ai's course.getCourseBySlug returns (trimmed).
const COURSE = {
  courseId: 1234, name: 'Agentic AI', slug: 'agentic-ai', type: 'course',
  wpData: { coursePartner: [{ title: 'DeepLearning.AI' }] },
  lessons: {
    pu5xbv: { index: 1, slug: 'pu5xbv', name: 'Welcome!', type: 'video', videoId: 11, time: 118, progress: 100 },
    ab12cd: { index: 2, slug: 'ab12cd', name: 'What is agentic AI?', type: 'video_notebook', videoId: 12, time: 640, progress: 100 },
    qz0001: { index: 3, slug: 'qz0001', name: 'Module 1 quiz', type: 'quiz', videoId: null, time: 300, progress: 100 },
    ef34gh: { index: 4, slug: 'ef34gh', name: 'Reflection', type: 'video', videoId: 13, time: 420, progress: 40 },
  },
  listing: [
    { moduleLabel: 'Module 1', name: 'Introduction to Agentic Workflows',
      content: [{ key: 'pu5xbv', type: 'lesson' }, { key: 'ab12cd', type: 'lesson' }, { key: 'qz0001', type: 'lesson' }] },
    { moduleLabel: 'Module 2', name: 'Reflection Design Pattern', content: [{ key: 'ef34gh', type: 'lesson' }] },
  ],
};

describe('DeepLearning.AI', () => {
  it('is its own platform', () => {
    expect(detectPlatform({ hostname: 'learn.deeplearning.ai', protocol: 'https:' })).toBe('deeplearning');
  });

  it('reads the course and lesson from the address', () => {
    expect(dlaiPath('/courses/agentic-ai/lesson/pu5xbv/welcome')).toEqual({ course: 'agentic-ai', lesson: 'pu5xbv' });
    expect(dlaiPath('/courses/agentic-ai')).toBeNull();
  });

  it('describes a lesson like any other lecture: course, module, number, length, link', () => {
    expect(dlaiLessonMeta(COURSE, 'agentic-ai', 'ab12cd')).toEqual({
      platform: 'deeplearning', course_id: 'agentic-ai', lecture_id: 'ab12cd',
      course_title: 'Agentic AI', lecture_title: 'What is agentic AI?', lecture_index: 2,
      section_title: 'Introduction to Agentic Workflows', section_index: 1, duration_sec: 640,
      url: 'https://learn.deeplearning.ai/courses/agentic-ai/lesson/ab12cd/what-is-agentic-ai',
      author: 'DeepLearning.AI',
    });
  });

  it('gives a one-module short course no section', () => {
    const short = { ...COURSE, listing: [{ moduleLabel: 'Module 1', name: null, content: [{ key: 'pu5xbv' }] }] };
    const meta = dlaiLessonMeta(short, 'agentic-ai', 'pu5xbv');
    expect(meta.section_title).toBeNull();
    expect(meta.section_index).toBeNull();
  });

  it('offers notes for the videos you finished, not quizzes or half-watched ones', () => {
    expect(dlaiFinished(COURSE, 'agentic-ai').map((m) => m.lecture_id)).toEqual(['pu5xbv', 'ab12cd']);
  });

  it('lists the caption files each video has', () => {
    const subtitle = JSON.stringify({
      'ja-jp': { URI: 'https://video.deeplearning.ai/x/subtitle/jp/a.vtt', NAME: 'JAPANESE' },
      'en-us': { URI: 'https://video.deeplearning.ai/x/subtitle/en/a.vtt', NAME: 'ENGLISH' },
    });
    expect(dlaiTracks(subtitle).map((t) => t.locale)).toEqual(['ja-jp', 'en-us']);
    expect(dlaiTracks(subtitle)[1].url).toBe('https://video.deeplearning.ai/x/subtitle/en/a.vtt');
    expect(dlaiTracks('not json')).toEqual([]);
    expect(dlaiTracks(null)).toEqual([]);
  });
});
